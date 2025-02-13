import re

from ..hacks import set_display_batch, set_display_name
from ..units import formatter_for_unit


def augment(benchmark, contexts=None):
    set_display_name(benchmark)
    set_display_time(benchmark)
    set_display_batch(benchmark)
    set_display_mean(benchmark)
    set_display_language(benchmark, contexts)
    set_display_error(benchmark)
    tags = benchmark["tags"]
    if "dataset" in tags:
        tags["dataset"] = dataset_name(tags["dataset"])


def dataset_name(name):
    return name.replace("_", " ")


def display_time(t):
    return t.split(".")[0].replace("T", "  ").rsplit(":", 1)[0] if t else ""


def set_display_language(benchmark, contexts):
    if contexts is not None and benchmark["links"]["context"] in contexts:
        url = benchmark["links"]["context"]
        benchmark["display_language"] = contexts[url]["benchmark_language"]
    else:
        benchmark["display_language"] = "unknown"


def set_display_time(benchmark):
    benchmark["display_timestamp"] = display_time(benchmark["timestamp"])


def set_display_mean(benchmark):
    if not benchmark["stats"]["mean"]:
        return ""

    unit = benchmark["stats"]["unit"]
    mean = float(benchmark["stats"]["mean"])
    fmt = formatter_for_unit(unit)
    benchmark["display_mean"] = fmt(mean, unit)


def set_display_error(benchmark):
    if not benchmark["error"]:
        benchmark["error"] = ""


def display_message(message):
    # truncate git shas in commit message
    for m in re.findall(r"\b[0-9a-f]{40}\b", message):
        message = message.replace(m, m[:7])
    return message
