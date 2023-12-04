# Author: Karl Gemayel
# Created: 2020-06-21, 6:27 p.m.

import logging
import math
import re
from subprocess import CalledProcessError

import numpy as np
import pandas as pd
from typing import *

from Bio.Align.Applications import ClustalOmegaCommandline
from Bio.Seq import Seq

from mg_container.genome_list import GenomeInfo
from mg_container.gms2_mod import GMS2Mod
from mg_container.msa import MSAType
from mg_general import Environment
from mg_general.general import get_value, os_join, run_shell_cmd
from mg_general.labels_comparison_detailed import LabelsComparisonDetailed
from mg_io.general import remove_p, load_obj
from mg_io.labels import read_labels_from_file, read_fgs_format, write_labels_to_file
from mg_io.shelf import write_sequence_list_to_fasta_file
from mg_models.gms2_noncoding import GMS2Noncoding
from mg_models.motif_model import MotifModel

log = logging.getLogger(__name__)


def bin_by_gc(df, step=1, **kwargs):
    # type: (pd.DataFrame, int, Dict[str, Any]) -> List[Tuple[float, float, pd.DataFrame]]

    gc_feature = get_value(kwargs, "gc_feature", "GC", valid_type=str)

    gc_ranges = range(30, 71, step)
    result = []
    a = 0
    for b in gc_ranges:
        right = b if b != gc_ranges[-1] else 100

        result.append(
            (a, b, df[(df[gc_feature] >= a) & (df[gc_feature] < right)])
        )
        a = b
    return result


def get_consensus_sequence(dict_mat):
    # type: (Dict[str, List[float]]) -> str

    num_positions = len(next(iter(dict_mat.values())))
    out = ""
    for n in range(num_positions):
        best_letter = None
        best_val = None

        for letter in dict_mat.keys():
            if (
                best_letter is not None
                and dict_mat[letter][n] > best_val
                or best_letter is None
            ):
                best_letter = letter
                best_val = dict_mat[letter][n]

        out += best_letter

    return out


def get_position_distributions_by_shift(df, col, shifts):
    # type: (pd.DataFrame, str, List[int]) -> Dict[int, List[Dict[int,str]]]

    result = dict()
    for n in range(len(df.index)):
        idx = df.index[n]
        s = shifts[n]

        if s not in result:
            result[s] = []

        result[s].append(df.at[idx, "Mod"].items[col])

    return result


def sort_sequences_by_first_non_gap_and_consensus(list_seqs):
    # type: (List[str]) -> List[str]

    def first_non_gap(l_seq):
        # type: (str) -> int
        for p in range(len(l_seq)):
            if l_seq[p] != "-":
                return p

        raise ValueError("Sequence is all gaps")

    pos_to_list_seqs = dict()
    for l in list_seqs:
        p = first_non_gap(l)
        if p not in pos_to_list_seqs.keys():
            pos_to_list_seqs[p] = []

        pos_to_list_seqs[p].append(l)

    # reappend into single list and sort per position
    output = []
    output_counts = []
    for p in sorted(pos_to_list_seqs.keys()):
        # get counts per item
        counter = Counter(pos_to_list_seqs[p])
        sorted_counter = sorted([(x, counter[x]) for x in counter], key=lambda v: v[0])

        output += [x[0] for x in sorted_counter]
        output_counts += [x[1] for x in sorted_counter]

    return output, output_counts


def print_reduced_msa(msa_t, sort_by_starting_position=False, n=None):
    # type: (MSAType, bool) -> str

    list_sequences = [x.seq._data for x in msa_t.list_alignment_sequences]

    if sort_by_starting_position:
        list_sequences, counts = sort_sequences_by_first_non_gap_and_consensus(list_sequences)

    out = ""
    counter = 0
    for s, c in zip(list_sequences, counts):
        out += f"{s}    {c}\n"

        if n is not None and counter >= n:
            break
        counter += 1

    return out


def create_extended_numpy_for_column_and_shifts(df, col, update_shifts, new_width):
    # type: (pd.DataFrame, str, List[int], int) -> np.ndarray
    # df = df[~df[col].isna()]  # we only need non-NA
    example = df.at[df.index[0], "Mod"].items[col]

    n = len(df)  # number of examples
    w = new_width
    b = len(example)  # number of bases (letters)

    mat = np.zeros((n, w, b), dtype=float)

    # fill the array
    for n_pos, idx in enumerate(df.index):
        dict_arr = df.at[idx, "Mod"].items[col]

        # for each base
        for b_pos, letter in enumerate(sorted(dict_arr.keys())):
            for w_pos, value in enumerate(dict_arr[letter]):
                shifted_w_pos = w_pos + update_shifts[n_pos]
                mat[n_pos, shifted_w_pos, b_pos] = value
    return mat


def run_msa_on_sequence_file(pf_fasta, pf_msa, **kwargs):
    # type: (str, str, Dict[str, Any]) -> None

    num_processors = get_value(kwargs, "num_processors", None)
    output_order = get_value(kwargs, "outputorder", "input-order")

    log.debug(f"Number of processors for MSA: {num_processors}")
    other_options = dict()
    if num_processors is not None:
        other_options["threads"] = num_processors

    clustalw_cline = ClustalOmegaCommandline(
        "clustalo", infile=pf_fasta, outfile=pf_msa,
        outputorder=output_order,
        force=True,
        outfmt="clustal",
        **other_options
    )

    clustalw_cline()


def run_msa_on_sequences(env, sequences, **kwargs):
    # type: (Environment, List[Seq], Dict[str, Any]) -> MSAType

    pd_work = env["pd-work"]
    fn_tmp_prefix = get_value(kwargs, "fn_tmp_prefix", "", default_if_none=True)

    # write sequences to file
    pf_fasta = os_join(pd_work, f"{fn_tmp_prefix}tmp_sequences.fasta")
    remove_p(pf_fasta)
    write_sequence_list_to_fasta_file(sequences, pf_fasta)

    # run msa
    pf_msa = os_join(pd_work, f"{fn_tmp_prefix}tmp_msa.txt")
    run_msa_on_sequence_file(pf_fasta, pf_msa, **kwargs)

    msa_t = MSAType.init_from_file(pf_msa)

    remove_p(pf_msa, pf_fasta)

    return msa_t


def gather_consensus_sequences(env, df, col):
    # type: (Environment, pd.DataFrame, str) -> List[str]

    sequences = []

    for idx in df.index:
        d = df.at[idx, "Mod"].items[col]  # type: Dict[str, List[float]]

        num_positions = len(next(iter(d.values())))
        out = ""
        for n in range(num_positions):
            best_letter = None
            best_val = None

            for letter in d.keys():
                if (
                    best_letter is not None
                    and d[letter][n] > best_val
                    or best_letter is None
                ):
                    best_letter = letter
                    best_val = d[letter][n]

            out += best_letter
        sequences.append(out)

    return sequences


def create_numpy_for_column_with_extended_motif(env, df, col, other=dict()):
    # type: (Environment, pd.DataFrame, str) -> np.ndarray

    example = df.at[df.index[0], "Mod"].items[col]

    # run alignment
    consensus_seqs = gather_consensus_sequences(env, df, col)
    msa_t = run_msa_on_sequences(env, consensus_seqs, outputorder="input-order")
    n = len(df)  # number of examples
    w = msa_t.alignment_length()
    b = len(example)  # number of bases (letters)
    other["msa_t"] = msa_t

    # get position of shift
    shifts = []
    for s in msa_t.list_alignment_sequences:
        p = next((pos for pos in range(len(s)) if s[pos] != "-"), 0)
        shifts.append(p)

    msa_t = run_msa_on_sequences(env, consensus_seqs, outputorder="tree-order")

    print_reduced_msa(msa_t, sort_by_starting_position=True)

    return create_extended_numpy_for_column_and_shifts(df, col, shifts, w), shifts

def create_numpy_for_column_by_clustering(env, df, col, other=dict()):
    # type: (Environment, pd.DataFrame, str) -> np.ndarray

    example = df.at[df.index[0], "Mod"].items[col]

    # run alignment
    consensus_seqs = gather_consensus_sequences(env, df, col)
    msa_t = run_msa_on_sequences(env, consensus_seqs, outputorder="input-order")
    n = len(df)  # number of examples
    w = msa_t.alignment_length()
    b = len(example)  # number of bases (letters)


    # get position of shift
    shifts = helper_clusters_by_heuristic(env, pd.DataFrame({"CONSENSUS_RBS_MAT": consensus_seqs}))
    w = len(next(iter(example.values()))) + max(shifts)

    shift_to_seqs = dict()
    shift_to_seq_to_count = dict()
    for s, c in zip(shifts, consensus_seqs):
        if s not in shift_to_seqs:
            shift_to_seqs[s] = set()
            shift_to_seq_to_count[s] = dict()
        if c not in shift_to_seq_to_count[s]:
            shift_to_seq_to_count[s][c] = 0

        shift_to_seqs[s].add(c)
        shift_to_seq_to_count[s][c] += 1


    for s in shift_to_seqs:
        shift_to_seqs[s] = sorted(shift_to_seqs[s])

    other["msa_t"] = (shift_to_seqs, shift_to_seq_to_count)


    return create_extended_numpy_for_column_and_shifts(df, col, shifts, w), shifts


def fix_genome_type(df):
    # type: (pd.DataFrame) -> None
    df["GENOME_TYPE"] = df["GENOME_TYPE"].apply(lambda x: x.strip().split("-")[1].upper())
    df.loc[df["GENOME_TYPE"] == "D2", "GENOME_TYPE"] = "D"


def read_archaea_bacteria_inputs(pf_arc, pf_bac):
    # type: (str, str) -> pd.DataFrame
    df_bac = load_obj(pf_bac)  # type: pd.DataFrame
    df_arc = load_obj(pf_arc)  # type: pd.DataFrame
    df_bac["Type"] = "Bacteria"
    df_arc["Type"] = "Archaea"

    df = pd.concat([df_bac, df_arc], sort=False, ignore_index=True)
    df["GENOME_TYPE"] = df.apply(lambda r: r["Mod"].items["GENOME_TYPE"], axis=1)
    # df["GC"] = df.apply(lambda r: r["Mod"].items["GC"], axis=1)
    fix_genome_type(df)

    return df


def key_to_gms2_tags(key):
    # type: (str) -> Set[str]

    return {
        "Start Codons": {"ATG", "GTG", "TTG"},
        "Stop Codons ": {"TAA", "TGA", "TAG"},
        "Start Context": {"SC_RBS", "SC_PROMOTER"},
        "RBS": {"RBS"},
        "Promoter": {"PROMOTER"},
        "Native": {"TO_NATIVE", "TO_MGM"}
    }[key]


def clean_up_start_context(mod_string, t, delete_sc):
    # type: (str, str, bool) -> str
    mod_string = re.sub(r"\$" + t + r" [^\n]+", "", mod_string)
    mod_string = re.sub(r"\$" + t + r"_ORDER [^\n]+", "", mod_string)
    mod_string = re.sub(r"\$" + t + r"_WIDTH [^\n]+", "", mod_string)
    mod_string = re.sub(r"\$" + t + r"_MARGIN [^\n]+", "", mod_string)
    mod_string = re.sub(r"\$" + t + r"_MAT[^\$]+", "", mod_string)

    if not delete_sc:
        mod_string += f"\n${t} 1\n${t}_ORDER 0\n${t}_WIDTH 1\n${t}_MARGIN -1\n${t}_MAT\nA 0.25\nC 0.25\nG 0.25\nT 0.25\n"
    return mod_string


def turn_off_components(pf_mod_original, pf_new_mod, components_off, native_coding_off=True):
    # type: (str, str, Iterable[str], bool) -> None

    with open(pf_mod_original, "r") as f:
        mod_string = f.read()

        turn_off_sc_for_rbs = "RBS" in components_off
        turn_off_sc_for_promoter = "Promoter" in components_off

        # change model based on components
        for coff in components_off:
            tags = key_to_gms2_tags(coff)

            if coff in {"Start Codons", "Stop Codons"}:
                for t in tags:
                    if re.findall(r"\$" + t + r"[\s\n]", mod_string):
                        mod_string = re.sub(r"\$" + t + r"\s+\d+\.\d+", f"${t} 0.333", mod_string)
            elif coff in {"RBS", "Promoter"}:
                for t in tags:
                    if re.findall(r"\$" + t + r"[\s\n]", mod_string):
                        mod_string = re.sub(r"\$" + t + r"\s+1", f"${t} 0", mod_string)
            elif coff in {"Start Context"}:
                for t in tags:
                    if re.findall(r"\$" + t + r"[\s\n]", mod_string):
                        delete_sc = turn_off_sc_for_rbs if t == "SC_RBS" else turn_off_sc_for_promoter
                        mod_string = clean_up_start_context(mod_string, t, delete_sc)

        if native_coding_off:
            mod_string = re.sub(
                r"\$TO_NATIVE" + r"\s+\d+\.\d+", "$TO_NATIVE 0.0", mod_string
            )
            mod_string = re.sub(r"\$TO_MGM" + r"\s+\d+\.\d+", "$TO_MGM 1.0", mod_string)

        with open(pf_new_mod, "w") as f_out:
            f_out.write(mod_string)


def run_gms2_prediction_with_model(env, pf_sequence, pf_new_mod, pf_new_pred, **kwargs):
    # type: (Environment, str, str, str, Dict[str, Any]) -> None

    prediction_format = get_value(kwargs, "format", "gff", valid_type=str)

    bin_external = env["pd-bin-external"]
    prog = f"{bin_external}/gms2/gmhmmp2"
    mgm_mod = f"{bin_external}/gms2/mgm_11.mod"
    cmd = f"{prog} -m {pf_new_mod} -M {mgm_mod} -s {pf_sequence} -o {pf_new_pred} --format {prediction_format}"
    run_shell_cmd(cmd)


def run_gms2_with_component_toggles_and_get_accuracy(env, gi, components_off, **kwargs):
    # type: (Environment, GenomeInfo, Set[str], Dict[str, Any]) -> Dict[str, Any]

    pf_mod_original = os_join(env["pd-runs"], gi.name, "gms2", "GMS2.mod")
    pf_reference = os_join(env["pd-data"], gi.name, "verified.gff")
    pf_sequence = os_join(env["pd-data"], gi.name, "sequence.fasta")
    pf_prediction = os_join(env["pd-work"], "prediction.gff")

    native_coding_off = get_value(kwargs, "native_coding_off", True)

    pf_new_mod = os_join(env["pd-work"], "model.mod")
    turn_off_components(pf_mod_original, pf_new_mod, components_off, native_coding_off=native_coding_off)

    done = False
    while not done:
        try:
            run_gms2_prediction_with_model(env, pf_sequence, pf_new_mod, pf_prediction)
            done = True
        except CalledProcessError:
            pass

    # compare with verified
    lcd = LabelsComparisonDetailed(read_labels_from_file(pf_reference), read_labels_from_file(pf_prediction))

    return {
        "Error": 100 - 100 * len(lcd.match_3p_5p('a')) / len(lcd.match_3p('a')),
        "Number of Errors": len(lcd.match_3p('a')) - len(lcd.match_3p_5p('a'))
    }


def component_in_model_file(env, gi, component):
    # type: (Environment, GenomeInfo, str) -> bool
    pf_mod = os_join(env["pd-runs"], gi.name, "gms2", "GMS2.mod")
    with open(pf_mod, "r") as f:
        mod_string = f.read()

        return any(
            re.findall(r"\$" + t + r"[\s\n]", mod_string)
            for t in key_to_gms2_tags(component)
        )


def run_mgm(env, pf_sequence, pf_mgm, pf_prediction, **kwargs):
    # type: (Environment, str, str, str) -> None
    # bin_external = env["pd-bin-external"]
    # prog = f"{bin_external}/gms2/gmhmmp2"
    # cmd = f"{prog} -M {pf_mgm} -s {pf_sequence} -o {pf_prediction} --format gff"
    # log.info(cmd)
    # run_shell_cmd(cmd)
    gcode = get_value(kwargs, "gcode", 11)
    fmt = get_value(kwargs, "fmt", "gff")

    bin_external = env["pd-bin-external"]
    prog = f"{bin_external}/mgm/gmhmmp"
    cmd = f"{prog} -v -m {pf_mgm} -o {pf_prediction} -f G {pf_sequence}"
    log.info(cmd)
    print(run_shell_cmd(cmd))

    # prog = f"{bin_external}/gms2/gmhmmp2"
    # pf_mgm = os_join(env["pd-bin-external"], "gms2", f"mgm_{gcode}.mod")
    # cmd = f"{prog} -M {pf_mgm} -s {pf_sequence} -o {pf_prediction} --format {fmt}"

    # run_shell_cmd(cmd)



def convert_fgs_to_gff(pf_input, pf_output):
    # type: (str, str) -> None
    labels = read_fgs_format(pf_input, shift=0)
    write_labels_to_file(labels, pf_output, shift_coordinates_by=0)

def run_fgs(env, pf_sequence, pf_prediction, **kwargs):
    # type: (Environment, str, str) -> None
    bin_external = env["pd-bin-external"]
    prog = f"cd {bin_external}/fgs; FragGeneScan"
    # prog=f"eval \"$(docker-machine env default)\"; docker run -v {env['pd-base']}:{env['pd-base']}  quay.io/biocontainers/fraggenescan:1.31--h516909a_2 " \
    #      f"run_FragGeneScan.pl -genome={pf_sequence} -complete=0" \
    #      f" -out={pf_prediction} -train=illumina_10"
    pf_mod = "complete"
    cmd = f"{prog} -s {pf_sequence} -o {pf_prediction} -w 1 -t complete"

    log.info(cmd)
    print(run_shell_cmd(cmd))
    convert_fgs_to_gff(pf_prediction + ".out", pf_prediction)
    run_shell_cmd(f"cp {pf_prediction} {pf_prediction}.copy")

def convert_mga_output_to_gff(output_str, pf_output):
    # type: (str, str) -> None
    i = 0
    with open(pf_output, "w") as outfile:

        print("##gff-version 3", file=outfile)  # GFF3 header
        for xx in output_str.split("\n"):

            if xx.startswith("#"):
                if not xx.startswith("# gc") and not xx.startswith("# self"):
                    seqid = xx[2:].strip().split()[0]
                    i += 1
            else:
                if len(xx.strip()) == 0:
                    continue
                (_, start, end, strand, frame, partial, score, _, _, _, _) = xx.split(
                    "\t"
                )


                frame = int(frame)
                start = int(start)
                end = int(end)
                if frame > 0:
                    if strand == "+":
                        start += frame
                    else:
                        end -= frame

                # if stop is partial, check if should be shifted
                if partial in ["10", "00"]:
                    gene_length = end - start + 1
                    rem = gene_length % 3
                    if rem > 0:
                        if strand == "+":
                            end -= rem
                        else:
                            start += rem

                # fix MGA's partial
                if partial == "11":
                    partial = "00"
                elif partial == "00":
                    partial == "11"
                elif partial == "10":
                    if strand == "+":
                        partial = "01"
                elif partial == "01":
                    if strand == "+":
                        partial = "10"


                print(
                    seqid,
                    "MGA",
                    "CDS",
                    start,
                    end,
                    score,
                    strand,
                    frame,
                    f"partial={partial}",
                    sep="\t",
                    file=outfile,
                )


def run_mga(env, pf_sequence, pf_prediction, **kwargs):
    # type: (Environment, str, str) -> None
    prog = "mga"
    cmd = f"{prog} -m {pf_sequence}"
    output = run_shell_cmd(cmd)
    convert_mga_output_to_gff(output, pf_prediction)
    run_shell_cmd(f"cp {pf_prediction} {pf_prediction}.copy")
    with open(f"{pf_prediction}.backup", "w") as f:
        f.write(output)
        f.close()

def run_tritisa(env, pf_sequence, pf_initial_labels, pf_prediction, **kwargs):
    # type: (Environment, str, str, str) -> None
    bin_external = env["pd-bin-external"]
    prog = f"cd ${bin_external}/tritisa/TriTISA"

def run_mgm2(env, pf_sequence, pf_mgm, pf_prediction, **kwargs):
    # type: (Environment, str, str, str) -> None

    gcode = get_value(kwargs, "gcode", None)
    fmt = get_value(kwargs, "fmt", "gff")

    bin_external = env["pd-bin-external"]
    prog = f"{bin_external}/mgm2/gmhmmp2"

    if gcode:
        pf_mgm = os_join(env["pd-bin-external"], "mgm2", f"mgm2_{gcode}.mod")

    cmd = f"{prog} -M {pf_mgm} -s {pf_sequence} -o {pf_prediction} --format {fmt}"

    print(run_shell_cmd(cmd))


def run_gms2(env, pf_sequence, pf_prediction, **kwargs):
    # type: (Environment, str, str, Dict[str, Any]) -> None

    genome_type = get_value(kwargs, "genome_type", "auto", valid_type=str)
    gcode = get_value(kwargs, "gcode", 11, valid_type=int)

    fmt = get_value(kwargs, "fmt", "gff")
    pe_tool = os_join(env["pd-bin-external"], "gms2", "gms2.pl")

    cmd_run = f"cd {env['pd-work']};\n"
    cmd_run += f"{pe_tool} --gcode {gcode} --format {fmt} --out {pf_prediction} --seq {pf_sequence}  "
    cmd_run += f"--v --genome-type {genome_type} --fgio-dist-thresh 25"

    run_shell_cmd(cmd_run)


def run_prodigal(env, pf_sequence, pf_prediction, **kwargs):
    # type: (Environment, str, str, Dict[str, Any]) -> None

    gcode = get_value(kwargs, "gcode", 11, valid_type=int)

    pe_tool = os_join(env["pd-bin-external"], "prodigal", "prodigal")

    cmd_run = f"cd {env['pd-work']};\n"
    cmd_run += f"{pe_tool}  -i {pf_sequence}  -g {gcode}  -o {pf_prediction}  -f gff  -q \n"

    run_shell_cmd(cmd_run)


def run_meta_prodigal(env, pf_sequence, pf_prediction, **kwargs):
    # type: (Environment, str, str, Dict[str, Any]) -> None

    gcode = get_value(kwargs, "gcode", 11, valid_type=int)

    pe_tool = os_join(env["pd-bin-external"], "prodigal", "prodigal")

    cmd_run = f"cd {env['pd-work']};\n"
    cmd_run += f"{pe_tool}  -i {pf_sequence}  -g {gcode}  -o {pf_prediction}  -f gff  -q -p meta \n"

    run_shell_cmd(cmd_run)

def run_meta_prodigal_autogcode(env, pf_sequence, pf_prediction, **kwargs):
    # type: (Environment, str, str, Dict[str, Any]) -> None

    pe_tool = os_join(env["pd-bin-external"], "prodigal", "prodigal")

    cmd_run = f"cd {env['pd-work']};\n"
    cmd_run += f"{pe_tool}  -i {pf_sequence}   -o {pf_prediction}  -f gff  -q -p meta \n"

    run_shell_cmd(cmd_run)


# def run_prodigal(env, gi, **kwargs):
#     # type: (Environment, GenomeInfo, Dict[str, Any]) -> None
#     pd_data = env["pd-data"]
#     pd_work = env["pd-work"]
#     pe_tool = os_join(env["pd-bin-external"], "prodigal", "prodigal")
#
#     pf_sequence = os_join(pd_data, gi.name, "sequence.fasta")
#
#     # FIXME: put in genetic code
#     cmd_run = "{}  -i {}  -g 11  -o prodigal.gff  -f gff  -t prodigal.parameters  -q \n".format(
#         pe_tool, pf_sequence
#     )
#     pf_pbs = os_join(pd_work, "run.pbs")
#     create_pbs_file(env, cmd_run, pf_pbs, job_name=gi.name, **kwargs)
#
#     run_shell_cmd("qsub {} &".format(pf_pbs))


def run_mgm_and_get_accuracy(env, gi, pf_mgm):
    # type: (Environment, GenomeInfo, str) -> Dict[str, Any]

    pf_prediction = os_join(env["pd-work"], "mgm_prediction.gff")
    run_mgm(env, os_join(env["pd-data"], gi.name, "sequence.fasta"), pf_mgm, pf_prediction)
    pf_reference = os_join(env["pd-data"], gi.name, "verified.gff")

    lcd = LabelsComparisonDetailed(read_labels_from_file(pf_reference), read_labels_from_file(pf_prediction))
    remove_p(pf_prediction)
    return {
        "Error": 100 - 100 * len(lcd.match_3p_5p('a')) / len(lcd.match_3p('a')),
        "Number of Errors": len(lcd.match_3p('a')) - len(lcd.match_3p_5p('a'))
    }


def run_mgm2_and_get_accuracy(env, gi, pf_mgm):
    # type: (Environment, GenomeInfo, str) -> Dict[str, Any]

    pf_prediction = os_join(env["pd-work"], "mgm_prediction.gff")
    run_mgm2(env, os_join(env["pd-data"], gi.name, "sequence.fasta"), pf_mgm, pf_prediction)
    pf_reference = os_join(env["pd-data"], gi.name, "verified.gff")

    lcd = LabelsComparisonDetailed(read_labels_from_file(pf_reference), read_labels_from_file(pf_prediction))
    remove_p(pf_prediction)
    try:
        return {
            "Error": 100 - 100 * len(lcd.match_3p_5p('a')) / len(lcd.match_3p('a')),
            "Number of Errors": len(lcd.match_3p('a')) - len(lcd.match_3p_5p('a'))
        }
    except ZeroDivisionError:
        return {"Error": np.nan, "Number of Errors": np.nan}


def train_gms2_model(env, pf_new_seq, pf_labels_lst, pf_mod, **kwargs):
    # type: (Environment, str, str, str, Dict[str, Any]) -> GMS2Mod

    group = get_value(kwargs, "group", "A", choices=list("ABCD") + ["X"])
    cmd = f"cd {env['pd-work']}; "
    cmd += f"{env['pd-bin-external']}/gms2/biogem gms2-training -s {pf_new_seq} -l {pf_labels_lst} -m {pf_mod} --order-coding 5 --order-noncoding 2 --only-train-on-native 1 --genetic-code 11 --order-start-context 2 --fgio-dist-thr 25 --genome-group {group} --ga-upstr-len-rbs 20 --align right --ga-width-rbs 6"
    run_shell_cmd(
        cmd
    )
    return GMS2Mod.init_from_file(pf_mod)


def relative_entropy(motif, background, component=None):
    # type: (MotifModel, GMS2Noncoding, str) -> float

    df_motif = motif.pwm_to_df()
    arr_bgd = background.pwm_to_array(0)

    result = 0.0

    if component in {"motif", "both", None}:
        for idx in df_motif.index:
            for i, l in enumerate(sorted(df_motif.columns)):
                result += df_motif.at[idx, l] * math.log2(df_motif.at[idx, l] / arr_bgd[i])

    if component in {"spacer", "both", None} and motif._spacer is not None:
        sp = motif._spacer
        sp_length = len(sp)
        for i in range(sp_length):
            result += sp[i] * math.log2(sp[i] / (1.0 / sp_length))

    return result


def run_mgm2_autogcode(env, pf_sequence, pf_prediction, **kwargs):
    # type: (Environment, str, str, Dict[str, Any]) -> None

    bin_external = env["pd-bin-external"]
    prog = f"{bin_external}/mgm2_auto/run_mgm.pl"

    p4 = get_value(kwargs, "p4", 10)
    p11 = get_value(kwargs, "p11", 20)

    if pf_summary := get_value(kwargs, "pf_summary", None):
        opt = f" --pf-summary {pf_summary} "
    else:
        opt = ""
    cmd = f"{prog} --seq {pf_sequence} --out {pf_prediction} --clean --p4 {p4} --p11 {p11} {opt}"
    run_shell_cmd(cmd)

def run_tool(env, pf_sequences, pf_prediction, tool, **kwargs):
    # type: (Environment, str, str, str, Dict[str, Any]) -> None

    tool = tool.lower()

    tool_to_func = {
        "mgm": run_mgm,
        "mgm2": run_mgm2,
        "gms2": run_gms2,
        "mprodigal": run_meta_prodigal,
        "prodigal": run_prodigal,
        "mga": run_mga,
        "fgs": run_fgs,
        "mgm2_autogcode": run_mgm2_autogcode,
        "mprodigal_autogcode": run_meta_prodigal_autogcode,
    }

    if tool not in tool_to_func:
        raise ValueError(f"Unknown tool {tool}. Accepted values: " + ",".join(sorted(tool_to_func.keys())))

    tool_to_func[tool](
        env,
        pf_sequence=pf_sequences,
        pf_prediction=pf_prediction,
        **kwargs
    )


def count_mismatches(s1, s2):
    # type: (str, str) -> int
    assert(len(s1) == len(s2))

    return sum(1 for i in range(len(s1)) if s1[i] != s2[i])


def helper_clusters_by_heuristic(env, df):
    # type: (Environment, pd.DataFrame) -> np.ndarray
    seqs = [df.loc[idx, "CONSENSUS_RBS_MAT"] for idx in df.index]
    clusters = [0] * len(seqs)

    freqs = df["CONSENSUS_RBS_MAT"].value_counts().to_dict()
    unique_seqs_ordered = list(
        sorted(freqs.keys(), key=lambda item: item[1], reverse=True)
    )


    seq_to_cluster = dict()
    cluster_to_seqs = dict()        # type: (Dict[int, List[str]])

    cluster_id = 0
    for item in unique_seqs_ordered:
        s = item

        # try and find an existing cluster
        found_id = None

        for curr_id in sorted(cluster_to_seqs.keys()):
            # make sure all seqs have the difference of 1
            mismatch_less_n = True
            for t in cluster_to_seqs[curr_id]:
                if count_mismatches(s, t) > 2:
                    mismatch_less_n = False

            if mismatch_less_n:
                found_id = curr_id
                break

        if found_id is not None:
            cluster_to_seqs[found_id].append(s)
            seq_to_cluster[s] = found_id
        else:
            cluster_to_seqs[cluster_id] = [s]
            seq_to_cluster[s] = cluster_id
            cluster_id += 1

    # put them back into original list
    for i in range(len(seqs)):
        clusters[i] = seq_to_cluster[seqs[i]]

    return np.array(clusters)
