def job_row(**over) -> dict:
    row = {
        "kind": "job",
        "key": "k",
        "shot": "s1",
        "round": 1,
        "seed": 7,
        "model": "ltx-2.5-draft",
        "gpu": "L4",
        "prompt": "p",
        "keyframe": "kf.png",
        "output": "out.mp4",
        "wall_s": 100.0,
        "ts": 0.0,
    }
    return row | over
