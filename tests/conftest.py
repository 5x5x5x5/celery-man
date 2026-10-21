import subprocess

import pytest


@pytest.fixture
def song(tmp_path_factory):
    path = tmp_path_factory.mktemp("song") / "song.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=40", str(path)], check=True)
    return path
