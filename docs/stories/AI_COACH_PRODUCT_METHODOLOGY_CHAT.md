# AI Coach Product Strategy & Physiology Methodology Chat

**Date Captured:** 27–29 September 2026
**Context:** Product definition, physiological foundations, Vekta reverse-engineering, and engineering feedback that originated the `release/traceable-physiology` epic.

---

## 1. Context & Business Principles

- **Problem Statement:** Passing raw, second-by-second FIT or CSV time-series data (e.g. 14,400 points for a 4-hour ride) to an LLM context window crashes tokens, increases latency, and costs excessive API spend.
- **Architectural Solution:** Separate the system into:
  1. **Deterministic Math Engine:** Runs offline / backend to calculate normalized metrics, time-in-zone, critical power curves, and W' balance into lightweight JSON summaries (15 MB FIT file -> 2 KB summary).
  2. **AI Reasoning Engine:** Reads deterministic summaries to provide context-aware coaching advice without needing raw data points.
- **Key Summary Metrics:**
  - **TSS & IF:** Overarching training stress and intensity factor.
  - **Total Kilojoules (kJ):** Mechanical work done. 1 kJ mechanical work ≈ 1 kcal metabolic expenditure (assuming ~20–25% gross mechanical efficiency). Essential for fueling recommendations.
  - **Normalized Power (NP):** Fatigue-weighted average power accounting for physiological variability.
  - **Time-in-Zone (TiZ):** Seconds spent across Coggan Zones 1–7 to identify targeted metabolic systems.
  - **Internal vs. External Load:** Power/Speed (external) vs. HR/RPE (internal). Decoupling flags acute fatigue, cardiac drift, or illness.

---

## 2. Reverse-Engineering Vekta: The "Secret Sauce"

1. **Dynamic Critical Power (CP) & Anaerobic Battery (W'):**
   - Moves beyond static 6-week FTP tests.
   - Continuously fits hyperbolic work-time curves using maximum efforts between 2 and 15 minutes over a rolling lookback window (e.g. 42 days).
2. **Separating Volume vs. Intensity:**
   - Volume = mechanical work done (kJ).
   - Intensity = physiological demand relative to current capacity (CP / CS).
3. **Durability (Fatigue Resistance):**
   - Measures how the power duration curve or peak power degrades after accumulating prior fatigue (e.g. peak 5-minute power after 1,500–2,500 kJ of work).
   - Division between Cycling and Running:
     - Cycling: Sustainable output in Watts (CP), battery in Joules (W'), work in kJ or kJ/kg.
     - Running: Sustainable output in m/s (Critical Speed - CS), battery in meters (D'), volume via distance/time-at-intensity.

---

## 3. Product Grooming & User Stories

### Epic: Dynamic Durability Profiling
The platform continuously models an athlete's physiological threshold (CP/CS) and evaluates degradation under accumulated work.

- **Story 1: Mean Maximal Power (MMP) Curve & Rolling Critical Power Fit**
  - Compute sliding-window power bests (e.g. 2m, 3m, 5m, 10m, 12m).
  - Fit work-time linear regression: $\text{Work} = CP \times t + W'$.
  - Extract $CP$ (slope) and $W'$ (intercept). Flag non-physical fits ($CP \le 0$ or $W' \le 0$).
- **Story 2: Real-Time Dynamic W' Balance (Skiba 2015 Differential Model)**
  - Track anaerobic battery replenishment and depletion per constant-power segment.
  - Depletion ($P > CP$): $\Delta B = -(P - CP) \Delta t$.
  - Reconstitution ($P < CP$): differential exponential recovery with $\tau = W' / (CP - P)$.
  - Record above-threshold match-burning events.
- **Story 3: Durability Curve Profiling**
  - Segment efforts by preceding work (e.g. Fresh < 500 kJ, Moderately Fatigued 1,000–1,500 kJ, Deeply Fatigued > 2,000 kJ).
  - Compare fresh vs. fatigued capacity.

---

## 4. Scientific Literature & Foundational Citations

1. **Monod & Scherrer (1965):**
   - *The Work Capacity of a Synergic Muscular Group.* Introduced the hyperbolic two-parameter critical power model.
2. **Skiba et al. (2012 / 2015):**
   - *Modeling the Expenditure and Reconstitution of Work Capacity Above Critical Power in Trained Cyclists.* Medicine & Science in Sports & Exercise (2012).
   - *Validation of a Novel Dynamic Model of W' Balance.* Sports Medicine Open / European Journal of Applied Physiology (2015).
3. **Maunder, Seiler, Mildenhall, Kilding, Plews (2021):**
   - *The Importance of ‘Durability’ in the Physiological Profiling of Endurance Athletes.* Sports Medicine. Introduced durability as a distinct, trainable physiological characteristic.
4. **Mateo-March et al. (2024):**
   - *Durability in Professional Cyclists: A Novel Paradigm.* Documenting power degradation after 1,000 kJ, 2,000 kJ, and 3,000 kJ.

---

## 5. Developer & Engineering Feedback (Contract Invariants)

Codex reviewed the initial proposals and instituted critical engineering guardrails:
1. **Reproducible Estimates vs. Probabilistic LLM Inference:**
   - Raw data must NEVER be passed to LLMs for arithmetic or threshold estimation.
   - Math must be strictly deterministic, reproducible, unit-tested, and versioned.
2. **Data Lifecycle & Ingestion Safety:**
   - Preserve immutable FIT artifacts with SHA-256 digests.
   - Enforce rate-limited upstream calls to Intervals.icu (e.g. 80 requests/run, durable cooldowns).
3. **Scientific Humility & Evidence Rules:**
   - Never equate model predictions with observed task failure (e.g. $W' < 10\%$ is an estimated battery state, not guaranteed collapse).
   - Gaps or dropouts in power invalidate subsequent $W'$ balance; never invent synthetic data.
   - Missing nutrition data is unknown; never issue automatic underfueling warnings.
   - Workload over 2,500 kJ across 3 days is a possible fatigue contributor, not proven causality.
   - Running $D'$ balance is deferred until running dynamics and grade-adjusted pace are standardized.

---

## 6. Staged Implementation Sequence

- **Stage 1: Repository Mapping & Calculation Contract** (Complete: `docs/PHYSIOLOGY.md`).
- **Stage 2: Data Foundation & FIT Normalization** (Complete: `physiology_samples.py`, `physiology_evidence.py`).
- **Stage 3: CP/CS Models & Snapshots** (Complete: `physiology_regression.py`, `physiology_models.py`).
- **Stage 4: Cycling W' Balance & Context Projection** (Complete: `physiology_balance.py`, `physiology_projection.py`, `physiology_rules.py`).
- **Stage 5: Durability & Evidence-Grounded Coaching** (In progress / ongoing integration).
