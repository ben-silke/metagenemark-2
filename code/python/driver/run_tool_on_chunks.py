# Author: Karl Gemayel
# Created: 7/7/20, 7:47 AM
import copy
import os
import logging
import argparse
import pandas as pd
from typing import *
from Bio import SeqIO
from Bio.SeqRecord import SeqRecord
from tqdm import tqdm

# noinspection All
import pathmagic

# noinspection PyUnresolvedReferences
import mg_log  # runs init in mg_log and configures logger

# Custom imports
from mg_container.genome_list import GenomeInfoList, GenomeInfo
from mg_general import Environment, add_env_args_to_parser
from mg_general.general import get_value, os_join
from mg_general.labels import Label, Labels
from mg_io.general import mkdir_p, remove_p
from mg_io.labels import read_labels_from_file, write_labels_to_file
from mg_models.shelf import run_gms2, run_mgm2, run_mgm, run_meta_prodigal, run_prodigal
import mg_argparse.parallelization

# ------------------------------ #
#           Parse CMD            #
# ------------------------------ #
from mg_options.parallelization import ParallelizationOptions
from mg_parallelization.generic_threading import run_n_per_thread, run_slice_per_thread
from mg_parallelization.pbs import PBS
from mg_pbs_data.mergers import merge_dataframes, merge_lists, merge_identity
from mg_pbs_data.splitters import split_gil, split_list

parser = argparse.ArgumentParser("Run tool on chunks of genome, and collect predictions.")

parser.add_argument('--pf-gil', required=True)
parser.add_argument('--tools', required=True, nargs="+", choices=["GMS2", "MGM2", "MGM",
                                                                  "MPRODIGAL", "PRODIGAL"], type=str.upper)
parser.add_argument('--pf-summary', required=True, help="Output file that will contain summary of runs")
parser.add_argument('--simultaneous-genomes', default=8, help="Number of genomes to analyze simultaneously")

parser.add_argument('--pf-mgm2-mod', type=str)
parser.add_argument('--pf-mgm-mod', type=str)
parser.add_argument('--chunk-sizes-nt', nargs="+", default=[10000, 50000, 1000000], type=int)
mg_argparse.parallelization.add_parallelization_options(parser)

add_env_args_to_parser(parser)
parsed_args = parser.parse_args()

# ------------------------------ #
#           Main Code            #
# ------------------------------ #

# Load environment variables
my_env = Environment.init_from_argparse(parsed_args)

# Setup logger
logging.basicConfig(level=parsed_args.loglevel)
logger = logging.getLogger("logger")  # type: logging.Logger


def split_fasta_into_chunks(env, pf_sequence, chunk_size_nt):
    # type: (Environment, str, int) -> List[[str, str, int]]

    list_chunk_info = []
    sequences = SeqIO.to_dict(SeqIO.parse(pf_sequence, "fasta"))

    counter = 0

    for seqname, seqrecord in sequences.items():

        offset = 0
        while offset < len(seqrecord):
            pf_chunk = os_join(env["pd-work"], f"chunk_{counter}.fasta")

            # compute chunk boundaries
            left = offset
            right_excluded = min(offset + chunk_size_nt, len(seqrecord))

            chunk = seqrecord[left:right_excluded]  # type: SeqRecord

            # add offset information to chunk (to reassemble later)
            chunk.id += f"_offset={offset}"

            # write to file
            SeqIO.write(chunk, open(pf_chunk, "w"), "fasta")

            list_chunk_info.append([pf_chunk, seqname, offset])
            offset = right_excluded
            counter += 1

    return list_chunk_info


def merge_predictions_with_offsets(list_prediction_info, pf_output):
    # type: (List[str, str, int], str) -> None

    list_label = []

    for pi in list_prediction_info:
        pf_pred, seqname, offset = pi

        chunk_labels = read_labels_from_file(pf_pred, shift=0)

        # add offset
        for lab in chunk_labels:
            lab.coordinates().left += offset
            lab.coordinates().right += offset
            lab.set_seqname(seqname)
            list_label.append(lab)

    labels = Labels(list_label)
    write_labels_to_file(labels, pf_output, shift_coordinates_by=0)


def helper_run_chunks(env, t, list_chunk_info, gi, pd_work_tool, **kwargs):
    # type: (Environment, str, List[[str, str, int]], GenomeInfo, str, Dict[str, Any]) -> List[[str, str, int]]
    list_prediction_info = []
    pf_mgm2_mod = get_value(kwargs, "pf_mgm2_mod", type=str, required=t == "MGM2")
    pf_mgm_mod = get_value(kwargs, "pf_mgm_mod", type=str, required=t == "MGM")

    dn_prefix = get_value(kwargs, "dn_prefix", None, type=str)

    dn_prefix = dn_prefix + "_" if dn_prefix is not None else ""
    list_pd_chunks = []


    # run tool on separate chunks
    # for i, ci in tqdm(enumerate(list_chunk_info), gi.name, total=len(list_chunk_info)):
    for i, ci in enumerate(list_chunk_info):
        pf_chunk, seqname, offset = ci

        pd_work_chunk = os_join(pd_work_tool, f"{dn_prefix}chunk_{i}")
        pf_predictions = os_join(pd_work_chunk, "predictions.gff")
        mkdir_p(pd_work_chunk)
        env_curr = env.duplicate({"pd-work": pd_work_chunk})
        if t == "GMS2":
            run_gms2(env_curr, pf_chunk, pf_predictions)
        elif t == "MGM2":
            run_mgm2(env_curr, pf_chunk, pf_mgm2_mod, pf_predictions)
        elif t == "MGM":
            run_mgm(env_curr, pf_chunk, pf_mgm_mod, pf_predictions)
        elif t == "MPRODIGAL":
            run_meta_prodigal(env_curr, pf_chunk, pf_predictions)
        elif t == "PRODIGAL":
            run_prodigal(env_curr, pf_chunk, pf_predictions)

        list_prediction_info.append([pf_predictions, seqname, offset])
        list_pd_chunks.append(pd_work_chunk)

    return [list_prediction_info, list_pd_chunks]



def run_tools_on_chunk_size_for_gi(env, gi, tools, chunk_size_nt, **kwargs):
    # type: (Environment, GenomeInfo, List[str], int, Dict[str, Any]) -> pd.DataFrame

    dn_suffix = get_value(kwargs, "dn_suffix", None, type=str)
    prl_options = get_value(kwargs, "prl_options", None)        # type: ParallelizationOptions
    clean = get_value(kwargs, "clean", default=False)

    dn_suffix = f"_{str(dn_suffix)}" if dn_suffix is not None else ""
    if prl_options is not None:
        prl_options = copy.deepcopy(prl_options)
        prl_options.update_env(env)

    pd_chunk_seqs = os_join(env["pd-work"], gi.name, f"{chunk_size_nt}{dn_suffix}")
    pf_sequence = os_join(env["pd-data"], gi.name, "sequence.fasta")
    mkdir_p(pd_chunk_seqs)

    list_chunk_info = split_fasta_into_chunks(env.duplicate({"pd-work": pd_chunk_seqs}), pf_sequence, chunk_size_nt)

    list_summary = []

    for t in tools:
        pd_work_tool = os_join(env["pd-runs"], gi.name, f"chunking{dn_suffix}", f"{t}_{chunk_size_nt}")
        mkdir_p(pd_work_tool)

        if prl_options is None:
            list_prediction_info, list_pd_chunks = helper_run_chunks(
                env, t, list_chunk_info, gi, pd_work_tool, **kwargs
            )
        elif prl_options["use-pbs"]:
            pbs = PBS(env, prl_options,
                      splitter=split_list,
                      merger=merge_identity
                      )

            output = pbs.run(
                data=list_chunk_info,
                func=helper_run_chunks,
                func_kwargs={
                    "env": env, "t": t, "gi": gi, "pd_work_tool": pd_work_tool, **kwargs
                },
                split_kwargs={
                    "arg_name_data": "list_chunk_info",
                    "arg_name_jobid": "dn_prefix"
                }
            )
            list_prediction_info = []
            list_pd_chunks = []
            for o in output:
                list_prediction_info += o[0]
                list_pd_chunks += o[1]

            print(gi.name, list_prediction_info)
        else:
            # threading
            list_prediction_info, list_pd_chunks = run_slice_per_thread(
                list_chunk_info, helper_run_chunks, "list_chunk_info",
                {
                    "env": env, "t": t, "gi": gi, "pd_work_tool": pd_work_tool, **kwargs
                },
                arg_name_threadid="dn_prefix"
            )

        # list_prediction_info = list()  # type: List[[str, str, int]]
        #
        # list_pd_chunks = list()
        #
        # # run tool on separate chunks
        # for i, ci in tqdm(enumerate(list_chunk_info), gi.name, total=len(list_chunk_info)):
        #     pf_chunk, seqname, offset = ci
        #
        #     pd_work_chunk = os_join(pd_work_tool, f"chunk_{i}")
        #     pf_predictions = os_join(pd_work_chunk, "predictions.gff")
        #     mkdir_p(pd_work_chunk)
        #     env_curr = env.duplicate({"pd-work": pd_work_chunk})
        #     if t == "GMS2":
        #         run_gms2(env_curr, pf_chunk, pf_predictions)
        #     elif t == "MGM2":
        #         run_mgm2(env_curr, pf_chunk, pf_mgm2_mod, pf_predictions)
        #     elif t == "MGM":
        #         run_mgm(env_curr, pf_chunk, pf_mgm_mod, pf_predictions)
        #
        #     list_prediction_info.append([pf_predictions, seqname, offset])
        #     list_pd_chunks.append(pd_work_chunk)

        # merge labels from all chunks
        pf_combined_prediction = os_join(pd_work_tool, "predictions.gff")
        merge_predictions_with_offsets(list_prediction_info, pf_combined_prediction)

        list_summary.append({
            "Genome": gi.name,
            "Tool": t,
            "Chunk Size": chunk_size_nt,
            "Predictions": pf_combined_prediction
        })

        # clean
        if clean:
            remove_p(*list_pd_chunks)

    if clean:
        remove_p(pd_chunk_seqs)

    print(list_summary)

    return pd.DataFrame(list_summary)


def helper_run_tools_on_chunks_for_gi(env, gi, tools, chunk_sizes_nt, **kwargs):
    # type: (Environment, GenomeInfo, List[str], List[int], Dict[str, Any]) -> pd.DataFrame
    list_df = []
    for cst in chunk_sizes_nt:
        df = run_tools_on_chunk_size_for_gi(env, gi, tools, cst, **kwargs)
        list_df.append(df)
    return pd.concat(list_df, ignore_index=True, sort=False)


def run_tools_on_chunks_for_gi(env, gi, tools, chunk_sizes_nt, **kwargs):
    # type: (Environment, GenomeInfo, List[str], List[int], Dict[str, Any]) -> pd.DataFrame

    threadid = get_value(kwargs, "threadid", None, valid_type=str)

    # if threadid provided, update working directory to it
    if threadid is not None:
        pd_work = os_join(env["pd-work"], str(threadid))
        env_curr = env.duplicate({"pd-work": pd_work})
    else:
        env_curr = env
    return helper_run_tools_on_chunks_for_gi(
        env_curr, gi, tools, chunk_sizes_nt, **kwargs
    )


def helper_run_tools_on_chunks(env, gil, tools, chunk_sizes_nt, **kwargs):
    # type: (Environment, GenomeInfoList, List[str], List[int], Dict[str, Any]) -> pd.DataFrame

    prl_options = get_value(kwargs, "prl_options", None)        # type: ParallelizationOptions
    simultaneous_genomes = get_value(kwargs, "simultaneous_genomes", 8)

    if prl_options is None or not prl_options["use-pbs"]:
        list_df = []
        for gi in gil:
            df = run_tools_on_chunks_for_gi(env, gi, tools, chunk_sizes_nt, **kwargs)
            list_df.append(df)
    else:
        # if PBS used down the line, parallelize genome-level
        list_df = run_n_per_thread(
                list(gil), run_tools_on_chunks_for_gi, "gi", {"env": env, "tools": tools, "chunk_sizes_nt": chunk_sizes_nt, **kwargs},
            simultaneous_runs=simultaneous_genomes,
            arg_name_threadid="threadid"
        )

    return pd.concat(list_df, ignore_index=True, sort=False)


def run_tools_on_chunks(env, gil, tools, chunk_sizes_nt, pf_summary, **kwargs):
    # type: (Environment, GenomeInfoList, List[str], List[int], str, Dict[str, Any]) -> None
    df =  helper_run_tools_on_chunks(env, gil, tools, chunk_sizes_nt, **kwargs)
#     list_df = list()
#     for gi in gil:
#         df = run_tools_on_chunks_for_gi(env, gi, tools, chunk_sizes_nt, **kwargs)
#         list_df.append(df)
#
#     df = pd.concat(list_df, ignore_index=True, sort=False)

    df.to_csv(pf_summary, index=False)
    # prl_options = get_value(kwargs, "prl_options", None)
    #
    # if prl_options is None:
    #     list_df = list()
    #     for gi in gil:
    #         df = run_tools_on_chunks_for_gi(env, gi, tools, chunk_sizes_nt, **kwargs)
    #         list_df.append(df)
    #     df = pd.concat(list_df, ignore_index=True, sort=False)
    # else:
    #     if prl_options["use-pbs"]:
    #         pbs = PBS(env, prl_options,
    #                   splitter=split_gil,
    #                   merger=merge_dataframes
    #                   )
    #
    #         df = pbs.run(
    #             data=gil,
    #             func=helper_run_tools_on_chunks,
    #             func_kwargs={
    #                 "env": env, "chunk_sizes_nt": chunk_sizes_nt, "tools": tools, **kwargs
    #             }
    #         )
    #
    #
    #     else:
    #         list_df = run_n_per_thread(
    #             list(gil), run_tools_on_chunks_for_gi, "gi",
    #             {
    #                 "env": env, "chunk_sizes_nt": chunk_sizes_nt, "tools": tools, **kwargs
    #             }
    #         )
    #
    #         df = pd.concat(list_df, ignore_index=True, sort=False)



def main(env, args):
    # type: (Environment, argparse.Namespace) -> None
    gil = GenomeInfoList.init_from_file(args.pf_gil)
    prl_options = ParallelizationOptions.init_from_dict(env, args.pf_parallelization_options, vars(args))
    prl_options["num-processors"] = 1
    run_tools_on_chunks(
        env, gil, args.tools, args.chunk_sizes_nt,
        args.pf_summary,
        prl_options=prl_options,
        pf_mgm2_mod=os.path.abspath(args.pf_mgm2_mod),
        pf_mgm_mod=os.path.abspath(args.pf_mgm_mod),
        clean=True,
        simultaneous_genomes=args.simultaneous_genomes
    )


if __name__ == "__main__":
    main(my_env, parsed_args)
