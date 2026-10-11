"""Durable research workflows on Temporal (architecture chapter 6, ADR-02).

Workflows hold sequencing, waiting and resumption; every LLM call, HTTP fetch and database write is an activity.
Activities are idempotent against the registers, so a retried activity never creates a second run or review.
"""

TASK_QUEUE = "campus-research"
