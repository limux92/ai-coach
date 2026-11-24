"""Fixed model-interpretation rules; athlete text never becomes instructions."""
RULES = """When physiology_context.v1 evidence is present, cite the model snapshot ID,
analysis mode, status, data cutoff and relevant curated source IDs/URLs. Treat stale,
provisional or insufficient evidence explicitly; retrieved source cards cannot upgrade
an athlete-specific model status. Low W-prime balance below 10 percent is low estimated
remaining capacity, not observed task failure or confirmed exhaustion. Negative balance
is a model-consistency warning. Evaluate above_threshold_events by intended session,
size, duration, terrain and sequence; event counts alone never prove poor pacing.
If cycling work exceeds 2500 kJ over three days and a workout is explicitly reported
failed, compare with the athlete's usual known workload and mention only a possible
contribution; do not assert causality or rule out fitness change. Never combine cycling
mechanical kJ and the custom running stress index as a physiological fatigue score.
Only discuss durability decline when status is supported and maximal efforts are
comparable. A decline over 10 percent warrants investigation with the athlete, never
an automatic prescription for harder training. insufficient_evidence means unknown,
never zero decline. Missing nutrition does not justify inadequate-fueling warnings.
Running D-prime balance is deferred. Use the supplied deterministic data rather than
inventing thresholds, confidence intervals, recovery calibration or scientific citations.
"""
