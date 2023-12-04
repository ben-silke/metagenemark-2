import logging
from typing import *
import seaborn

logger = logging.getLogger(__name__)


seaborn.set_palette(seaborn.xkcd_palette(["windows blue", "amber", "faded green", "dusty purple"]))


def _init_mapping_ancestors():
    colors = ["windows blue", "amber", "faded green", "dusty purple"]
    ancestors = ["Archaea", "Actinobacteria", "Enterobacterales", "FCB group"]
    palette = seaborn.xkcd_palette(colors)
    return {x[0]: x[1] for x in zip(ancestors, palette)}

def _init_mapping_verified():
    colors = ["windows blue", "amber", "faded green", "dusty purple", "pale red"]
    ancestors = ["E. coli", "H. salinarum", "N. pharaonis", "M. tuberculosis", "R. denitrificans"]
    palette = seaborn.xkcd_palette(colors)
    return {x[0]: x[1] for x in zip(ancestors, palette)}

def _init_mapping_gms2_components():
    colors = ["windows blue", "amber", "faded green", "dusty purple", "pale red", "beige", "cyan"]
    ancestors = ["MGM", "Start Codons", "Promoter", "RBS", "Start Context", "MGM2*", "GMS2"]
    palette = seaborn.xkcd_palette(colors)
    return {x[0]: x[1] for x in zip(ancestors, palette)}


def _init_mapping_independence_conditions():
    colors = ["windows blue", "amber", "faded green"]
    conditions = ["Random", "Independent", "Fully dependent"]
    palette = seaborn.xkcd_palette(colors)
    return {x[0]: x[1] for x in zip(conditions, palette)}

def _init_mapping_archea_bacteria():
    colors = ["magenta", "windows blue"]
    name = ["Archaea", "Bacteria"]
    palette = seaborn.xkcd_palette(colors)
    return {x[0]: x[1] for x in zip(name, palette)}


def _init_mapping_start_codons():
    colors = ["windows blue", "amber", "faded green"]
    conditions = ["ATG", "GTG", "TTG"]
    palette = seaborn.xkcd_palette(colors)
    return {x[0]: x[1] for x in zip(conditions, palette)}

def _init_mapping_stop_codons():
    colors = ["windows blue", "amber", "faded green"]
    conditions = ["TAG", "TGA", "TAA"]
    palette = seaborn.xkcd_palette(colors)
    return {x[0]: x[1] for x in zip(conditions, palette)}

def _init_mapping_tools():
    colors = ["windows blue", "amber", "faded green", "dusty purple", "pale red", "dark olive", "grey", "black", "orange", "amber"]
    ancestors = ["mgm", "mgm2", "gms2", "mprodigal", "prodigal", "fgs", "mga", "ncbi", "verified", "mgm2_auto"]

    colors += colors
    ancestors += [x.upper() for x in ancestors]
    palette = seaborn.xkcd_palette(colors)

    return {x[0]: x[1] for x in zip(ancestors, palette)}




class ColorMap:

    _mappings = {
        "ancestor": _init_mapping_ancestors(),
        "independence-conditions": _init_mapping_independence_conditions(),
        "arc-bac": _init_mapping_archea_bacteria(),
        "verified": _init_mapping_verified(),
        "starts": _init_mapping_start_codons(),
        "stops": _init_mapping_stop_codons(),
        "gms2_components": _init_mapping_gms2_components(),
        "tools": _init_mapping_tools()
    }

    @staticmethod
    def get_map(name):
        # type: (str) -> Dict[str, Any]

        if name not in ColorMap._mappings:
            raise ValueError(f"Unknown color mapping for: {name}")

        return ColorMap._mappings[name]


