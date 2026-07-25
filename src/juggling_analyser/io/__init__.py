"""Ingestion adapters. Every adapter yields a source-agnostic
:class:`juggling_analyser.core.trajectory.Session`."""

from .qtm import read_qtm

__all__ = ["read_qtm"]
