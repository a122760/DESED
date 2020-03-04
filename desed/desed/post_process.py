"""Post processing of desed synthetic generation of soundscapes"""
import glob
import inspect
import os
import shutil
from os import path as osp

import jams
import numpy as np
import pandas as pd
import soundfile as sf
from .logger import create_logger
from .utils import create_folder


def rm_high_polyphony(folder, max_polyphony=3, save_tsv_associated=None, pattern_sources="_events"):
    """ Remove the files having a too high polyphony in the deignated folder

    Args:
        folder: str, path to the folder containing scaper generated sounds (JAMS files) in which to remove the files.
        max_polyphony: int, the maximum number of sounds that can be heard at the same time (polyphony).
        save_tsv_associated: str, optional, the path to generate the tsv files of associated sounds.

    Returns:
        None

    """
    logger = create_logger(__name__ + "/" + inspect.currentframe().f_code.co_name)
    # Select training
    i = 0
    df = pd.DataFrame(columns=['scaper', 'bg', 'fg'])
    fnames_to_rmv = []
    for jam_file in sorted(glob.glob(osp.join(folder, "*.jams"))):
        param = jams.load(jam_file)
        ann = param.annotations.search(namespace='scaper')[0]
        if ann['sandbox']['scaper']['polyphony_max'] <= max_polyphony:
            fg = [osp.basename(line.value['source_file']) for line in ann.data]
            bg = osp.basename(ann.data[0].value['source_file'])
            fname = osp.basename(jam_file)
            df_tmp = pd.DataFrame(np.array([[fname, bg, ",".join(fg)]]), columns=['scaper', 'bg', 'fg'])
            df = df.append(df_tmp, ignore_index=True)
            i += 1
        else:
            fnames_to_rmv.append(jam_file)
    if save_tsv_associated is not None:
        df.to_csv(save_tsv_associated, sep="\t", index=False)

    logger.warning(f"{i} files with less than {max_polyphony} overlapping events. Deleting others...")
    for fname in fnames_to_rmv:
        names = glob.glob(osp.splitext(fname)[0] + ".*")
        for file in names:
            os.remove(file)
        dirs_sources = glob.glob(osp.splitext(fname)[0] + pattern_sources)
        for dir_path in dirs_sources:
            shutil.rmtree(dir_path)


def sanity_check(df, length_sec=None):
    """ Check that onset and offset are in the boundaries
    Args:
        df: pandas.DataFrame, dataframe defining 'onset' and 'offset' columns.
        length_sec: float, optional, if defined it is the maximum length of a file.

    Returns:
        pandas.DataFrame, the updated dataframe.
    """
    if length_sec is not None:
        df['offset'].clip(upper=length_sec, inplace=True)
    df['onset'].clip(lower=0, inplace=True)
    df = df.round(3)
    return df


def get_data(file, wav_file=None, background_label=False):
    """ Get annotation of a file (txt or JAMS) and check the correspondance with a wav file (created by Scaper).
    Args:
        file: str, path of the .txt or .jams file.
        wav_file: str, path of the wav file associated with the 'file'.
        background_label: bool, whether to get the background as a label or not.

    Returns:

    """
    if wav_file is not None:
        data, sr = sf.read(wav_file)
        length_sec = data.shape[0] / sr
    else:
        length_sec = None

    fn, ext = osp.splitext(file)
    if ext == ".txt":
        if background_label:
            raise NotImplementedError("Impossible to add the background event from the txt file. "
                                      "Information not in the txt file")
        df = pd.read_csv(file, sep='\t', names=["onset", "offset", "event_label"])
    elif ext == ".jams":
        df = get_labels_from_jams(file, background_label)
    else:
        raise NotImplementedError("Only txt and jams generated by Scaper can be loaded with get_data")

    return df, length_sec


def _post_process_labels_file(df_ann, length_sec=None, min_dur_event=0.250, min_dur_inter=0.150, rm_nOn_n_Off=True):
    """ Check the annotations,
        * Merge overlapping annotations of the same class
        * Merge overlapping annotations having less than 150ms between them (or 400ms between the onsets).
        * Make minimum length of events = 250ms.
    Args:
        df_ann:
        length_sec:
        min_dur_event:
        min_dur_inter:

    Returns:

    """
    df = df_ann.copy()
    if rm_nOn_n_Off:
        df["event_label"] = df["event_label"].apply(lambda x: x.replace("_nOff", "").replace("_nOn", ""))
    logger = create_logger(__name__ + "/" + inspect.currentframe().f_code.co_name)
    fix_count = 0
    df = sanity_check(df, length_sec)
    df = df.sort_values('onset')
    for class_name in df['event_label'].unique():
        logger.debug(class_name)
        i = 0
        while i is not None:
            indexes = df[df['event_label'] == class_name].index
            ref_onset = df.loc[indexes[i], 'onset']
            ref_offset = df.loc[indexes[i], 'offset']
            if ref_offset - ref_onset < min_dur_event:
                ref_offset = ref_onset + min_dur_event
                # Too short event, and at the offset (onset sorted),
                # so if it overlaps with others, they are also too short.
                if ref_offset > length_sec:
                    df = df.drop(indexes[i:])
                    fix_count += len(indexes[i:])
                    break
                else:
                    df.loc[indexes[i], 'offset'] = ref_onset + min_dur_event
            j = i + 1
            while j < len(indexes):
                if df.loc[indexes[j], 'offset'] < ref_offset:
                    df = df.drop(indexes[j])
                    logger.debug("Merging overlapping annotations")
                    fix_count += 1
                elif df.loc[indexes[j], 'onset'] - ref_offset < min_dur_inter:
                    df.loc[indexes[i], 'offset'] = df.loc[indexes[j], 'offset']
                    ref_offset = df.loc[indexes[j], 'offset']
                    df = df.drop(indexes[j])
                    logger.debug("Merging consecutive annotation with pause" + "<150ms")
                    fix_count += 1
                elif df.loc[indexes[j], 'onset'] - ref_onset < min_dur_event + min_dur_inter:
                    df.loc[indexes[i], 'offset'] = df.loc[indexes[j], 'offset']
                    ref_offset = df.loc[indexes[j], 'offset']
                    df = df.drop(indexes[j])
                    logger.debug("Merging consecutive annotations" + " with onset diff<400ms")
                    fix_count += 1
                else:
                    # Quitting the loop
                    break
                j += 1
            i += 1
            if i >= len(df[df['event_label'] == class_name].index):
                i = None
    df = df.sort_values('onset')
    return df, fix_count


def post_process_df_labels(df, files_duration=None, output_tsv=None, min_dur_event=0.250,
                           min_dur_inter=0.150, rm_nOn_nOff=False):
    """ clean the .txt files of each file. It is the same processing as the real data
        - overlapping events of the same class are mixed
        - if silence < 150ms between two conscutive events of the same class, they are mixed
        - if event < 250ms, the event lasts 250ms

        Args:
            df: pd.DataFrame, dataframe of annotations containing columns ["filename", "onset", "offset", "event_label"]
            files_duration: pd.DataFrame or float, dataframe containing columns ["filename", "duration"]
                indicating the lengh of a file.
                or float being the length of all the files if all the files have the same duration.
            output_tsv: str, optional, tsv with all the annotations concatenated
            min_dur_event: float, optional in sec, minimum duration of an event
            min_dur_inter: float, optional in sec, minimum duration between 2 events
            rm_nOn_nOff: bool, whether to delete the additional _nOn _nOff at the end of labels.

        Returns:
            None
        """
    logger = create_logger(__name__ + "/" + inspect.currentframe().f_code.co_name)
    fix_count = 0
    logger.info("Correcting annotations ... \n"
                "* annotations with negative duration will be removed\n" +
                "* annotations with duration <250ms will be extended on the offset side)")

    result_df = pd.DataFrame()
    for fn in df.filename.unique():
        logger.debug(fn)
        if files_duration is not None:
            if type(files_duration) is pd.DataFrame:
                length_sec = files_duration[files_duration.filename == fn].duration
            elif type(files_duration) in [float, int]:
                length_sec = files_duration
            else:
                raise TypeError("files duration is pd.DataFrame or a float only")
        else:
            length_sec = None
        df_ann, fc = _post_process_labels_file(df[df.filename == fn], length_sec, min_dur_event,
                                               min_dur_inter, rm_nOn_nOff)
        fix_count += fc

        result_df = result_df.append(df_ann[['filename', 'onset', 'offset', 'event_label']], ignore_index=True)

    if output_tsv:
        result_df.to_csv(output_tsv, index=False, sep="\t", float_format="%.3f")

    logger.info(f"================\nFixed {fix_count} problems\n================")
    return result_df


def post_process_txt_labels(txtdir, wavdir=None, output_folder=None, output_tsv=None, min_dur_event=0.250,
                            min_dur_inter=0.150, background_label=False, rm_nOn_nOff=False):
    """ clean the .txt files of each file. It is the same processing as the real data
    - overlapping events of the same class are mixed
    - if silence < 150ms between two conscutive events of the same class, they are mixed
    - if event < 250ms, the event lasts 250ms

    Args:
        txtdir: str, directory path where the XXX.txt files are.
        wavdir: str, directory path where the associated XXX.wav audio files are (associated with .txt files)
        output_folder: str, optional, folder in which to put the checked files
        output_tsv: str, optional, tsv with all the annotations concatenated
        min_dur_event: float, optional in sec, minimum duration of an event
        min_dur_inter: float, optional in sec, minimum duration between 2 events
        background_label: bool, whether to include the background label in the annotations.
        rm_nOn_nOff: bool, whether to delete the additional _nOn _nOff at the end of labels.

    Returns:
        None
    """
    logger = create_logger(__name__ + "/" + inspect.currentframe().f_code.co_name)
    if wavdir is None:
        wavdir = txtdir
    fix_count = 0
    logger.info("Correcting annotations ... \n"
                "* annotations with negative duration will be removed\n" +
                "* annotations with duration <250ms will be extended on the offset side)")

    if output_folder is not None:
        create_folder(output_folder)

    df_single = pd.DataFrame()  # only useful if output_csv defined

    if background_label:
        list_files = glob.glob(osp.join(txtdir, "*.jams"))
    else:
        list_files = glob.glob(osp.join(txtdir, "*.txt"))
        if len(list_files) == 0:
            list_files = glob.glob(osp.join(txtdir, '*.jams'))

    out_extension = '.txt'
    for fn in list_files:
        logger.debug(fn)
        df, length_sec = get_data(fn, osp.join(wavdir, osp.splitext(osp.basename(fn))[0] + '.wav'),
                                  background_label=background_label)

        df, fc = _post_process_labels_file(df, length_sec, min_dur_event, min_dur_inter, rm_nOn_nOff)
        fix_count += fc

        if output_folder is not None:
            filepath = os.path.splitext(os.path.basename(fn))[0] + out_extension
            df[['onset', 'offset', 'event_label']].to_csv(osp.join(output_folder, filepath),
                                                          header=False, index=False, sep="\t")
        if output_tsv is not None:
            df['filename'] = osp.join(osp.splitext(osp.basename(fn))[0] + '.wav')
            df_single = df_single.append(df[['filename', 'onset', 'offset', 'event_label']], ignore_index=True)

    if output_tsv:
        df_single.to_csv(output_tsv, index=False, sep="\t", float_format="%.3f")

    logger.info(f"{fix_count} problems Fixed")


def get_labels_from_jams(jam_file, background_label=False, return_length=False):
    tsv_data = []
    param = jams.load(jam_file)
    ann = param['annotations'][0]
    for obs in ann.data:
        if obs.value['role'] == 'foreground' or (background_label and obs.value['role'] == 'background'):
            tsv_data.append(
                [obs.time, obs.time + obs.duration, obs.value['label']])
    df = pd.DataFrame(tsv_data, columns=["onset", "offset", "event_label"])

    if return_length:
        return df, ann.duration
    else:
        return df