import logging
from typing import *
import seaborn
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.font_manager import FontProperties

from mg_general.general import get_value, next_name
from mg_container.msa import MSAType
import logomaker as lm

from mg_models.mgm_motif_model_v2 import MGMMotifModelV2
from mg_models.shelf import print_reduced_msa

log = logging.getLogger(__name__)


class MGMMotifModelVisualizerV2:

    @staticmethod
    def _viz_letter(mgm_mm, letter, ax, shift):
        # type: (MGMMotifModelV2, str, plt.Axes, int) -> None

        x = range(mgm_mm.motif_width())
        y = [mgm_mm._motif[shift][letter][p] for p in x]

        seaborn.lineplot(x, y, ax=ax, color="blue")

    @staticmethod
    def _viz_motif_pwm(mgm_mm, axes, shift):
        # type: (MGMMotifModelV2, List[plt.Axes], int) -> None

        ylim = [-0.1, 1.1]
        letters = sorted(mgm_mm._motif[shift].keys())

        # for each letter
        for l, ax in zip(letters, axes):
            MGMMotifModelVisualizerV2._viz_letter(mgm_mm, l, ax, shift)
            ax.set_title(f"{l}")
            ax.set_ylim(*ylim)

    @staticmethod
    def _viz_logo(mgm_mm, ax, shift):
        # type: (MGMMotifModelV2, plt.Axes, int) -> None

        # seqs = [x.seq._data for x in msa_t.list_alignment_sequences]
        # counts_mat = lm.alignment_to_matrix(sequences=seqs, to_type='counts', characters_to_ignore='.-X')

        # Counts matrix -> Information matrix
        bgd = [0.25] * 4
        # if "avg_gc" in mgm_mm._kwargs:
        #     gc = mgm_mm._kwargs["avg_gc"] / 100.0
        #     g = c = gc / 2.0
        #     at = 1 - gc
        #     a = at / 2.0
        #     t = 1 - a - g - c
        #     bgd = [a, c, g, t]
        try:

            info_mat = lm.transform_matrix(mgm_mm.pwm_to_df(shift),
                                       from_type='probability',
                                       to_type='information',
                                       background=mgm_mm._kwargs["avg_bgd"])
        except Exception:
            return

        # df = mgm_mm.pwm_to_df()
        #
        # for idx in df.index:
        #     for c in df.columns:
        #         df.at[idx, c] = math.log2(4) - df.at[idx, c] * math.log2(df.at[idx, c])
        #

        lm.Logo(info_mat, ax=ax, color_scheme="classic")
        ax.set_ylim(*[0, 2])

    @staticmethod
    def _viz_msa(msa_t, ax):
        # type: (MSAType, plt.Axes) -> None

        fp = FontProperties()
        fp.set_family("monospace")

        ax.text(0, 0, print_reduced_msa(msa_t, True, n=10),
                horizontalalignment='left',
                verticalalignment='center',
                fontproperties=fp, usetex=False)

        ax.set_xlim(*[-0.2, 0.4])
        ax.set_ylim(*[-0.4, 0.4])
        ax.get_xaxis().set_visible(False)
        ax.get_yaxis().set_visible(False)

    @staticmethod
    def _viz_spacer(mgm_mm, ax):
        # type: (MGMMotifModelV2, plt.Axes) -> None

        for s in sorted(mgm_mm._spacer.keys()):
            dist = mgm_mm._spacer[s]

            seaborn.lineplot(range(len(dist)), dist, label=s, ax=ax)

        ax.set_xlabel("Distance from gene start")
        ax.set_ylabel("Probability")

    @staticmethod
    def _viz_prior(mgm_mm, ax, cluster_by=None):
        # type: (MGMMotifModelV2, plt.Axes, str) -> None

        n_show = 3
        x = sorted(mgm_mm._shift_prior.keys())
        y = [mgm_mm._shift_prior[a] for a in x]

        if len(x) < n_show:
            for i in range(len(x), n_show):
                x += [i]
                y += [0]

        x = [int(a) for a in x]
        y = [a / float(sum(y)) for a in y]

        seaborn.barplot(x, y, ax=ax, color="blue")
        ax.set_ylabel("Probability")
        if cluster_by is None or cluster_by == "msa":
            ax.set_xlabel("Shift")
        else:
            ax.set_xlabel("Cluster")

        ax.set_ylim(0, 1)

    @staticmethod
    def visualize(mgm_mm, title="", **kwargs):
        # type: (MGMMotifModelV2, str, Dict[str, Any]) -> None

        msa_t = get_value(kwargs, "msa_t", None)
        raw_motif_data = get_value(kwargs, "raw_motif_data", None)
        pd_figures = get_value(kwargs, "pd_figures", ".")
        cluster_by = get_value(kwargs, "cluster_by", valid=["msa", "heuristic"])

        num_shifts = len(mgm_mm._shift_prior.keys())

        fig = plt.figure(figsize=(14, 4 * num_shifts))
        shape = (num_shifts + 1, 5)

        # for each shift
        for s in range(num_shifts):
            # create consensus, followed by box plots
            ax_logo = plt.subplot2grid(shape, (s, 0))
            axes_box = [plt.subplot2grid(shape, (s, i)) for i in range(1, 5)]

            MGMMotifModelVisualizerV2._viz_logo(mgm_mm, ax_logo, s)

            if raw_motif_data is None:
                MGMMotifModelVisualizerV2._viz_motif_pwm(mgm_mm, axes_box, s)
            elif s in raw_motif_data:
                MGMMotifModelVisualizerV2._viz_motif_pwm_from_raw_data(raw_motif_data[s], axes_box,
                                                                       mgm_mm.motif_width())

        # last row: MSA, shift prior, spacers
        ax_text = plt.subplot2grid(shape, (num_shifts, 0))
        ax_counts = plt.subplot2grid(shape, (num_shifts, 1))
        ax_pos_dist = plt.subplot2grid(shape, (num_shifts, 2))

        MGMMotifModelVisualizerV2._viz_spacer(mgm_mm, ax_pos_dist)
        MGMMotifModelVisualizerV2._viz_prior(mgm_mm, ax_counts, cluster_by=cluster_by)

        if cluster_by == "msa" and msa_t is not None:
            MGMMotifModelVisualizerV2._viz_msa(msa_t, ax_text)
        elif cluster_by == "heuristic" and msa_t is not None:
            MGMMotifModelVisualizerV2._viz_heuristic(msa_t, ax_text)


        plt.suptitle(f"Gc range: {title}")

        plt.tight_layout()
        plt.subplots_adjust(top=0.9)
        plt.savefig(next_name(pd_figures))
        plt.close()

        # plt.show()

    @staticmethod
    def _viz_motif_pwm_from_raw_data(raw_motif_data, axes, motif_width):
        # type: ([np.ndarray, List[int]], List[plt.Axes], int) -> None
        letters = list("ACGT")
        letter_to_idx = {x: i for i, x in enumerate(letters)}
        array = raw_motif_data

        ylim = [-0.1, 1.1]

        for l, ax in zip(letters, axes):
            # for each position in motif
            # go through df and accumulate values
            all_positions = []
            all_probs = []
            for w_pos in range(array.shape[1]):

                # go through all motifs at current position
                for index in range(array.shape[0]):

                    w_pos

                    all_positions.append(w_pos)

                    if array[index, w_pos, letter_to_idx[l]] < 0 or array[
                        index, w_pos, letter_to_idx[l]] > 1:
                        raise ValueError("Something's up")
                    all_probs.append(array[index, w_pos, letter_to_idx[l]])

                # ax.scatter(all_gc, all_probs, marker="+")
                # seaborn.regplot(all_gc, all_probs, ax=ax, lowess=True, scatter_kws={"s": 5, "alpha": 0.3})
            ax.set_title(f"{l}")

            df = pd.DataFrame({"Position": all_positions, "Probability": all_probs})
            df.sort_values("Position", inplace=True)

            df_mean = df.groupby("Position", as_index=False).mean()
            seaborn.boxplot("Position", "Probability", data=df, ax=ax, color="red", fliersize=0)
            seaborn.lineplot(df_mean["Position"], df_mean["Probability"], ax=ax, color="blue")
            ax.set_ylim(*ylim)

    @classmethod
    def _viz_heuristic(cls, clustering, ax):
        # type: ([Dict[int, List[str]], Dict[int, Dict[str, int]]], plt.Axes) -> None
        fp = FontProperties()
        fp.set_family("monospace")

        out = ""
        for c in sorted(clustering[0]):
            out += f"Cluster {c}\n"
            for seq in sorted(clustering[0][c], key=lambda x: clustering[1][c][x], reverse=True):
                out += f"{seq}   {clustering[1][c][seq]}\n"
            out += "\n"

        ax.text(0.3, 0.4, out,
                horizontalalignment='left',
                verticalalignment='center', transform=ax.transAxes,
                fontproperties=fp, usetex=False)

        # ax.set_xlim(*[-0.4, 0.4])
        # ax.set_ylim(*[-0.4, 0.4])
        ax.get_xaxis().set_visible(False)
        ax.get_yaxis().set_visible(False)