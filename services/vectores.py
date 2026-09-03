# -*- coding: utf-8 -*-
"""Operaciones vectoriales mínimas (sin numpy) para la recuperación semántica."""
import math


def coseno(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    punto = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return punto / (na * nb) if na and nb else 0.0
