"""Read the Room — ambient room-sensing engine.

Layered RoomState producers over live audio: continuous DSP (loudness,
activity, spectral balance), a Silero VAD gate (speech ratio), and a VAD-gated
wav2vec2 emotion layer (valence/arousal), composed into a rolling-window
RoomState stream.
"""

from .config import Config
from .engine import Consumer, Engine
from .state import HeadcountBucket, RoomState

__version__ = "0.1.0"
__all__ = ["Config", "Consumer", "Engine", "HeadcountBucket", "RoomState", "__version__"]
