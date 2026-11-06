"""Model / threshold evaluation against v1 judgments (design §7.6). Read-only; writes a report."""

from __future__ import annotations

import json
import random
from contextlib import closing
from datetime import UTC, datetime

import numpy as np

from visualstore.db import fingerprints as fp
from visualstore.domain.bands import BANDS, band, thresholds_from
from visualstore.jobs.io.import_v1 import ImportError_, _open_v1, gid
from visualstore.jobs.scheduler import IoContext
from visualstore.models.registry import get_spec

MIN_POSITIVES = 20
RECALLS = (0.90, 0.95, 0.99)
MAX_SHA1_PAIRS = 20_000


class EvalError(Exception):
    pass


def _open_v1_or_error(path: str):
    try:
        return _open_v1(path)
    except ImportError_ as exc:
        raise EvalError(str(exc)) from exc


def _load_vectors(vconn, model_id: str, ids: set[bytes]) -> dict[bytes, np.ndarray]:
    out: dict[bytes, np.ndarray] = {}
    for fid in ids:
        rows = fp.frames_of_file(vconn, fid, model_id)
        if rows:
            out[fid] = np.frombuffer(rows[0]["vector"], dtype=np.float16).astype(np.float32)
    return out


def _distances(pairs_: list[tuple[bytes, bytes]], vectors: dict[bytes, np.ndarray]) -> np.ndarray:
    return np.array([1.0 - float(np.dot(vectors[a], vectors[b])) for a, b in pairs_], dtype=float)


def _stats(d: np.ndarray) -> dict:
    if d.size == 0:
        return {"count": 0}
    hist, _ = np.histogram(np.clip(d, 0, 1), bins=20, range=(0.0, 1.0))
    return {
        "count": int(d.size),
        "mean": round(float(d.mean()), 4),
        "median": round(float(np.median(d)), 4),
        "p05": round(float(np.quantile(d, 0.05)), 4),
        "p95": round(float(np.quantile(d, 0.95)), 4),
        "histogram": hist.tolist(),
    }


def _auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Probability that a random positive is closer than a random negative (rank statistic)."""
    all_d = np.concatenate([pos, neg])
    ranks = np.argsort(np.argsort(all_d)) + 1  # 1-based, smaller distance = smaller rank
    pos_ranks = float(ranks[: pos.size].sum())
    return float(1.0 - (pos_ranks - pos.size * (pos.size + 1) / 2) / (pos.size * neg.size))


def _collect_labels(conn, v1, rnd: random.Random):
    have = {
        bytes(r["id"]) for r in conn.execute("SELECT id FROM file WHERE state = 'fingerprinted'")
    }
    positives: set[tuple[bytes, bytes]] = set()
    negatives: set[tuple[bytes, bytes]] = set()
    orphans = 0
    rows = v1.execute(
        "SELECT File1Id, File2Id, IgnoredMode FROM SimilarFile WHERE IgnoredMode IN (1, 2)"
    )
    for row in rows:
        a, b = gid(row["File1Id"]), gid(row["File2Id"])
        if a not in have or b not in have or a == b:
            orphans += 1
            continue
        (positives if row["IgnoredMode"] == 1 else negatives).add(tuple(sorted((a, b))))
    sha_pairs: list[tuple[bytes, bytes]] = []
    buckets = conn.execute(
        "SELECT group_concat(hex(id), ',') AS ids FROM file WHERE sha1 IS NOT NULL"
        " AND state = 'fingerprinted' GROUP BY sha1 HAVING COUNT(*) > 1"
    )
    for bucket in buckets:
        members = [bytes.fromhex(h) for h in bucket["ids"].split(",")]
        sha_pairs.extend(tuple(sorted((members[0], m))) for m in members[1:])
    rnd.shuffle(sha_pairs)
    positives.update(sha_pairs[:MAX_SHA1_PAIRS])
    return positives, negatives, orphans


def _random_negatives(conn, rnd: random.Random, want: int, positives: set) -> set:
    by_folder: dict[bytes, list[bytes]] = {}
    for r in conn.execute("SELECT id, folder_id FROM file WHERE state = 'fingerprinted'"):
        by_folder.setdefault(bytes(r["folder_id"]), []).append(bytes(r["id"]))
    folders = [f for f, ids in by_folder.items() if ids]
    out: set[tuple[bytes, bytes]] = set()
    attempts = 0
    while len(folders) >= 2 and len(out) < want and attempts < want * 20:
        attempts += 1
        fa, fb = rnd.sample(folders, 2)
        pair = tuple(sorted((rnd.choice(by_folder[fa]), rnd.choice(by_folder[fb]))))
        if pair not in positives:
            out.add(pair)
    return out


def run_eval(ctx: IoContext) -> dict:
    spec = get_spec(ctx.settings["model.id"], ctx.model_dir)
    rnd = random.Random(int(ctx.params.get("seed", 1)))
    want_random = int(ctx.params.get("negatives", 5000))
    with (
        closing(ctx.open_metadata()) as conn,
        closing(ctx.open_vectors()) as vconn,
        closing(_open_v1_or_error(ctx.params["db_path"])) as v1,
    ):
        positives, negatives, orphans = _collect_labels(conn, v1, rnd)
        ctx.report(
            {
                "done": 0,
                "total": 0,
                "message": f"{len(positives)} positives, {len(negatives)} labelled negatives",
            }
        )
        random_negatives = _random_negatives(conn, rnd, want_random, positives)
        needed = {f for p in positives | negatives | random_negatives for f in p}
        vectors = _load_vectors(vconn, spec.id, needed)

        def keep(pairs_):
            return [p for p in pairs_ if p[0] in vectors and p[1] in vectors]

        pos, neg_lab, neg_rnd = keep(positives), keep(negatives), keep(random_negatives)
        if len(pos) < MIN_POSITIVES:
            raise EvalError(
                f"only {len(pos)} labelled positive pairs have vectors for {spec.id};"
                f" at least {MIN_POSITIVES} are needed"
            )
        d_pos = _distances(pos, vectors)
        d_lab = _distances(neg_lab, vectors)
        d_rnd = _distances(neg_rnd, vectors)
        d_neg = np.concatenate([d_lab, d_rnd])

        fpr_at_recall = {}
        for r in RECALLS:
            thr = float(np.quantile(d_pos, r))
            fpr_at_recall[f"{r:.2f}"] = {
                "threshold": round(thr, 4),
                "labelled": round(float((d_lab <= thr).mean()), 4) if d_lab.size else None,
                "random": round(float((d_rnd <= thr).mean()), 4) if d_rnd.size else None,
            }
        t = thresholds_from(ctx.settings)
        sets = (("positives", d_pos), ("negatives_labelled", d_lab), ("negatives_random", d_rnd))
        bands_current = {
            name: {
                b: round(float(np.mean([band(x, False, t) == b for x in d])), 4) if d.size else None
                for b in BANDS[1:]
            }
            for name, d in sets
        }
        pooled = float(np.sqrt((d_pos.var() + (d_neg.var() if d_neg.size else 0.0)) / 2)) or 1.0
        summary = {
            "model_id": spec.id,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "positives": len(pos),
            "negatives_labelled": len(neg_lab),
            "negatives_random": len(neg_rnd),
            "orphans": orphans,
            "distances": {name: _stats(d) for name, d in sets},
            "fpr_at_recall": fpr_at_recall,
            "separation_dprime": (
                round(float((d_neg.mean() - d_pos.mean()) / pooled), 3) if d_neg.size else None
            ),
            "auc": round(_auc(d_pos, d_neg), 4) if d_neg.size else None,
            "bands_current": bands_current,
            "thresholds_current": {"near_duplicate": t.near_duplicate, "variant": t.variant},
            "suggested": {
                "near_duplicate": fpr_at_recall["0.95"]["threshold"],
                "variant": fpr_at_recall["0.99"]["threshold"],
            },
        }
        reports = ctx.paths.root / "reports"
        reports.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = reports / f"eval-{spec.id}-{stamp}.json"
        md_path = reports / f"eval-{spec.id}-{stamp}.md"
        json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        md_path.write_text(_markdown(summary), encoding="utf-8")
        summary["report_json"] = str(json_path)
        summary["report_md"] = str(md_path)
        return summary


def _markdown(s: dict) -> str:
    lines = [
        f"# Evaluation of {s['model_id']}",
        "",
        f"Generated {s['generated_at']}.",
        "",
        f"- Positive pairs (v1 related + byte-identical): {s['positives']}",
        f"- Labelled negative pairs (v1 unrelated): {s['negatives_labelled']}",
        f"- Random cross-Folder negative pairs: {s['negatives_random']}",
        f"- v1 rows skipped (missing files): {s['orphans']}",
        "",
        "## Distances",
        "",
        "| set | count | mean | median | p05 | p95 |",
        "|---|---|---|---|---|---|",
    ]
    for name, st in s["distances"].items():
        if st.get("count"):
            cells = (name, st["count"], st["mean"], st["median"], st["p05"], st["p95"])
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines += [
        "",
        "## False-positive rate at fixed recall",
        "",
        "| recall | threshold | labelled negatives | random negatives |",
        "|---|---|---|---|",
    ]
    for r, v in s["fpr_at_recall"].items():
        lines.append(f"| {r} | {v['threshold']} | {v['labelled']} | {v['random']} |")
    cur = s["thresholds_current"]
    sug = s["suggested"]
    lines += [
        "",
        f"Separation d' = {s['separation_dprime']}, AUC = {s['auc']}.",
        "",
        f"Current thresholds: near_duplicate {cur['near_duplicate']}, variant {cur['variant']}.",
        f"Suggested: near_duplicate {sug['near_duplicate']} (95 % recall),"
        f" variant {sug['variant']} (99 % recall).",
        "",
    ]
    return "\n".join(lines)
