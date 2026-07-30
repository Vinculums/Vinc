# Page curve

## One-line essence
The Page curve is the answer forced by the unitarity of quantum mechanics to the question of how the von Neumann entropy of Hawking radiation must evolve in time — Page 1993 predicted its quantitative form, the RT/QES of AdS/CFT + the island formula of Hawking radiation made an exact derivation possible in 2019, and the 2020 replica wormholes refine the microscopic mechanism. The first full-fledged quantum-gravity result in which the partial solution of the 50-year information paradox appeared as an integration of quantum information and spacetime geometry.

## Physical picture

**What unitarity forces**: quantum-mechanical evolution is unitary ($|\psi\rangle \to U|\psi\rangle$). The von Neumann entropy of a closed system is conserved. If BH formation + evaporation = a closed quantum system, then initial pure state → final pure state (information preserved). Hawking's 1976 information loss violates unitarity, so it **must be refuted** when quantum mechanics and quantum gravity are combined.

**Page 1993 — the quantitative curve prediction**:
The time evolution of the von Neumann entropy $S_R(t)$ of the region $R$ where Hawking radiation is collected:
- initially ($t = 0$): $R$ is empty, $S_R = 0$
- monotonic-increase regime ($0 < t < t_P$): Hawking radiation accumulates and entangles with the BH. Entropy $\approx S_{\rm rad}(t)$ (thermal entropy)
- **Page time** $t_P$: the moment the BH has half evaporated. **Entropy maximum $\approx S_{BH}^{\rm initial}/2$**
- monotonic-decrease regime ($t_P < t < t_{\rm evap}$): the BH-side dimension becomes the limit and $S_R$ decreases. $\approx S_{BH}(t)$ (BH area/4G)
- finally ($t = t_{\rm evap}$): $S_R = 0$ (a pure state is recovered, information fully recovered)

The curve's shape: a hat, or a tent. The naive Hawking gives only a monotonically increasing line → unitarity violation. **Page predicted the curve's form via quantum information theory; the quantum-gravity mechanism was unknown.**

**Analogy — the crossing of two thermodynamic limits**:
- the entropy of a small subsystem is bounded by the smaller dimension: $S \leq \min(\ln d_R, \ln d_B)$
- early on $d_R$ is small so $S \approx \ln d_R$ (monotonic increase)
- later $d_B$ is small so $S \approx \ln d_B$ (monotonic decrease)
- the moment the two limits cross is the Page time

**The island formula (2019) — quantum gravity's answer**:
The island formula seen in Hawking radiation exactly reproduces the Page curve. The key mechanism:
- the naive-Hawking regime ($t < t_P$): island $I = \emptyset$ is the extremum → monotonic increase (recovering Hawking's result)
- after the Page time: a non-empty island $I$ is a smaller extremum → the entropy monotonically decreases
- the island = a BH interior region, **holding the information of the Hawking radiation $R$** (like a quantum error-correcting code)

**Replica wormholes (2020) — the microscopic mechanism**:
When computing $\text{Tr}\,\rho_R^n$, Euclidean quantum gravity has two saddles:
1. **Disconnected geometry**: the $n$ copies are independent → the monotonic Hawking result
2. **Replica wormhole**: wormhole connections among the copies → the island formula (the Page-curve part)

The Page time = the moment the actions of the two saddles become equal; afterward the wormhole dominates. **The wormhole topology of Euclidean quantum gravity is the exact microscopic mechanism of information recovery.**

**Key insight**: the "uncorrelated blackbody Hawking radiation" shown by Hawking 1976 is only a first approximation. The quantum corrections (island + replica wormhole) add subtle correlations that encode the information. **Hawking was not wrong; he saw only a part.**

## Mathematical structure

**von Neumann entropy** (the reduced density matrix of the Hawking radiation $R$):
$$S_R = -\text{Tr}(\rho_R \ln \rho_R),\qquad \rho_R = \text{Tr}_{\bar R}|\psi\rangle\langle\psi|$$

**The naive Hawking result** (first approximation):
$$S_R^{\rm Hawking}(t) \approx s\cdot t,\qquad s = \text{the entropy production rate of Hawking radiation}$$
Monotonically increasing in time, finally $S_R \to S_{BH}^{\rm initial}$ — **unitarity violation.**

**Page 1993 — the information-theoretic bound**:
For a random pure state $|\psi\rangle \in \mathcal H_R \otimes \mathcal H_B$ (of dimension $d_R \cdot d_B$):
$$S_R \leq \ln(\min(d_R, d_B))$$
The average (the Page average):
$$\langle S_R\rangle \approx \ln d_R - \frac{d_R}{2 d_B}\quad (d_R \leq d_B)$$
$$\langle S_R\rangle \approx \ln d_B - \frac{d_B}{2 d_R}\quad (d_R \geq d_B)$$

**BH dimension**: $d_B \sim \exp(S_{BH}) \sim \exp(A/4G\hbar)$
**Hawking-radiation dimension**: $d_R$ grows with time

**Page time**: $d_R = d_B \Rightarrow S_R^{\rm rad}(t_P) = S_{BH}(t_P)$
- with the BH evaporation rate + monotonicity one derives $t_P \approx t_{\rm evap}/2$

**Island formula** (Penington 2019, AEMM 2019):
$$\boxed{S(R) = \min_I\,\text{ext}_I\left[\frac{A(\partial I)}{4 G_N} + S_{\rm bulk}^{\rm ent}(R \cup I)\right]}$$
- $R$: the boundary Hawking-radiation region
- $I$: the bulk island (the region enclosed by the Quantum Extremal Surface)
- $\partial I$: the island boundary
- $S_{\rm bulk}^{\rm ent}$: the von Neumann entropy of the bulk quantum fields

**Derivation steps**:
1. try $I = \emptyset$: $S(R) = S_{\rm bulk}(R) \approx S_{\rm rad}^{\rm Hawking}$ (the Hawking result)
2. try $I \neq \emptyset$: $S(R) = A(\partial I)/(4G) + S_{\rm bulk}(R \cup I)$
3. take $\min$ → the smaller dominates

**Post-Page-time analysis**:
- the BH area decreases → $A(\partial I)/(4G) \to 0$ (the smaller the BH, the smaller the island boundary)
- $R \cup I$ complements the information → $S_{\rm bulk}(R \cup I) \to 0$
- result: $S(R) \to 0$ (a pure state is recovered)

**Replica trick**:
$$S_R = -\lim_{n \to 1}\frac{\partial}{\partial n}\text{Tr}\,\rho_R^n = -\lim_{n \to 1}\frac{1}{n-1}\ln\text{Tr}\,\rho_R^n$$

**Replica wormhole** (PSSY 2020, AHMS 2020):
$$\text{Tr}\,\rho_R^n = \int\mathcal D g\,e^{-S_E[g]}\bigg|_{\text{$n$-copy branching}}$$

The two Euclidean saddle points:
1. **Disconnected**: the copies are independent
$$Z_{\rm disc} \sim Z_1^n \;\Rightarrow\; S_R \sim n\ln Z_1\quad (\text{monotonic Hawking})$$

2. **Wormhole connected** (the $n$ copies connected by a wormhole):
$$Z_{\rm worm} \sim Z_n \;\Rightarrow\; S_R \sim S_{\rm island}\quad (\text{the island formula})$$

**Saddle-dominance conditions**:
- $t < t_P$: $Z_{\rm disc} > Z_{\rm worm}$ → monotonic Hawking
- $t > t_P$: $Z_{\rm worm} > Z_{\rm disc}$ → the island formula (Page curve)

**JT gravity + SYK** (a 1+1-dimensional toy model):
$$S_{\rm JT} = -\frac{1}{16\pi G}\int d^2x\sqrt{-g}\,\phi(R + 2/L^2) + \text{boundary}$$

Saad-Shenker-Stanford 2019: exactly computed the wormhole contribution to the JT partition function. The SYK model (Sachdev-Ye-Kitaev fermions) plays the role of the boundary CFT.

**Use of the Cardy formula**:
$$S(E) \approx 2\pi\sqrt{\frac{cE}{6}}\quad (\text{2D CFT})$$
Used in the microscopic derivation of large-BH entropy.

## Why it matters

**A partial solution of Hawking's 1976 50-year information paradox**:
- 1976: the information-loss claim (Hawking)
- 1993: the Page-curve prediction (assuming information recovery)
- 2019: the island formula exactly derives the Page curve for AdS BHs — **information recovery quantitatively established in the AdS domain**
- 2020: the microscopic mechanism refined via replica wormholes
- unsolved: generalization to flat and dS BHs (the BHs of our universe)

**The first full-fledged quantum-gravity result**:
- before: fragmentary results such as Hawking radiation and BH entropy
- 2019–: the AdS/CFT dictionary + the island formula industrialized as quantum-gravity calculation tools
- **quantum gravity elevated from conjecture to a computational discipline** — at least in the AdS domain

**The information-theoretic meaning of the Bekenstein-Hawking entropy**:
- $S_{BH} = A/(4G\hbar)$ — Hawking's 1974 derivation
- 1996 Strominger-Vafa: string-theory D-brane microscopic counting
- 2019 the island formula: refining **the information-theoretic meaning of $S_{BH}$ as the von Neumann entropy of BH microstates**

**Verification of the change in Hawking's own position**:
- 1976 the information-loss claim
- 2004 conceding the Preskill bet (the possibility of information recovery)
- 2018 the last paper on soft hair
- 2019 posthumously — the quantitative derivation verifies his agreement to recovery

**The most impressive application of AdS/CFT**:
- an abstract correspondence partially solves a 50-year-unsolved problem
- the integration of quantum information + spacetime geometry + Euclidean quantum gravity

**Unsolved**:
- **generalization to flat-space and dS BHs** — the BHs of our universe are asymptotically flat
- **the Lorentzian interpretation of the replica wormhole** — exact only in Euclidean
- **a clear picture of the black-hole interior spacetime** — fuzzball? smooth? firewall?
- **the microscopic mechanism of information recovery** — how do the Hawking-radiation particles come out carrying the BH interior information?

## Historical context

**Act I — Page's quantitative prediction (1993)**:
- 1976 Hawking — *Breakdown of predictability* PRD 14. Formalization of the information paradox
- 1985 't Hooft — the black-hole horizon as a quantum membrane
- 1990 Susskind — string theory and BH information
- **1993 Page** — *Information in black hole radiation* PRL 71, 3743 (arXiv:hep-th/9306083) + *Average entropy of a subsystem* PRL 71, 1291. Naming the **Page curve + Page time.** A quantitative curve prediction, the quantum-gravity mechanism unknown
- **The decisive turn**: 1993 — Page predicts the quantitative curve. Clarifying **the target of the following 26 years.** Any quantum-gravity candidate must derive this curve

**Act II — AdS/CFT and the emergence of tools (1996–2014)**:
- 1996 Strominger-Vafa — D-brane BH microscopic counting. Exactly reproducing Bekenstein-Hawking. The first microscopic evidence of the possibility of information recovery
- 1997 Maldacena AdS/CFT — boundary unitarity → bulk quantum-gravity unitarity. Information recovery in principle
- 2003 't Hooft / Susskind complementarity — the infalling observer vs the external observer
- 2006 Ryu-Takayanagi — entanglement entropy = minimal area
- 2007 HRT — the time-dependent generalization
- 2012 AMPS Firewall — the collision of monogamy, the equivalence principle, and locality
- 2013 Maldacena-Susskind ER=EPR — the firewall response
- 2014 Almheiri-Dong-Harlow — bulk reconstruction
- **The decisive turn**: 2006 RT + 2012 firewall — tools capable of computing the Page curve in quantum gravity begin to appear. AMPS clarifies the consistency problem of the naive Hawking

**Act III — the island formula and the partial solution (2019)**:
- 2014 Engelhardt-Wall — the Quantum Extremal Surface (QES). The quantum generalization of RT
- **May 2019 Penington** (arXiv:1905.08255) — *Entanglement Wedge Reconstruction and the Information Paradox*. The first appearance of the **island formula**
- **May 2019 AEMM** (Almheiri-Engelhardt-Marolf-Maxfield, arXiv:1905.08762) — *The entropy of bulk quantum fields and the entanglement wedge of an evaporating black hole*. Independent derivation
- 2019– an explosion in the field — various scenarios of the island formula (1+1, 4D, rotating BHs, charged BHs, etc.)
- **The decisive turn**: May 2019 — AEMM and Penington independently announce the island formula in the same month. The 50-year information paradox gets a partial solution via quantum information + AdS/CFT. Quantum gravity goes from conjecture to a computational discipline

**Act IV — the replica-wormhole microscopic mechanism (2019–)**:
- 2019 Saad-Shenker-Stanford — the JT gravity + SYK toy model (arXiv:1903.11115)
- **2020 PSSY** (Penington-Shenker-Stanford-Yang, arXiv:1911.11977) — the formalization of replica wormholes
- **2020 AHMS** (Almheiri-Hartman-Maldacena-Shaghoulian-Tajdini, arXiv:1911.12333) — independent derivation, **the Euclidean quantum-gravity interpretation of replica wormholes**
- 2021 Almheiri et al. — *The entropy of Hawking radiation*, the comprehensive RMP review
- 2022– JT + matrix models, the factorization paradox, ensemble vs UV CFT
- 2024– generalization to flat space, dS, and non-AdS (mostly unsolved)
- **The decisive turn**: 2020 — the exact microscopic mechanism of information recovery established via replica wormholes. Hawking 1976 the partial statement, Page 1993 the target, AEMM/Penington 2019 the exact derivation, PSSY/AHMS 2020 the microscopic mechanism — **the moment 27 years of accumulation combined explosively**

**Key insight**: the Page curve was a **27-year target.** Page drew the curve in 1993, but which mechanism of quantum gravity produces it was unknown. 1997 AdS/CFT, 2006 RT, 2014 QES, 2019 the island formula, 2020 replica wormholes — almost every quantum-information + quantum-gravity tool had to accumulate before a quantitative derivation was possible. **Hawking 1976 was not wrong but a first approximation** — the quantum corrections recover the information. Hawking himself agreed to information recovery in 2004, but he did not live to see the exact derivation of 2019.

## Connections

**Prior connections** (already studied):
- Hawking radiation — the formal definition of the island formula, the 4 BH laws, the 50-year information paradox. **A direct bridge** (this note is the quantitative-derivation detail of 17)
- AdS/CFT — RT/HRT/QES/HaPPY as the formal tools of the island formula. **A direct bridge** (dictionary + formalism)
- Quantum Information — von Neumann entropy, unitarity, entanglement, the naming of the Page-curve prediction. Quantum-information tools as the language of information recovery
- General Relativity — Schwarzschild and Kerr BHs, the event horizon, surface gravity
- Quantum Field Theory — QFT on curved spacetime, Bogoliubov, the definition of the vacuum
- Statistical Mechanics — the partition function, the informational meaning of the Bekenstein-Hawking entropy
- Quantum Mechanics — unitarity, measurement, closed-system evolution, tensor product, the reduced density matrix

**Next connections** (proceeding into full quantum gravity):
- **the depth of replica wormholes** (a new P2 follow-up) — exact analysis of JT gravity + SYK, the factorization paradox
- **the string-theory landscape** (new P2) — refinement of Strominger-Vafa BH counting, D-brane microstates
- **LQG·CDT alternatives** (P3) — the BH information processing of non-string quantum gravity
- **generalization to flat and dS** (new P2, unsolved) — application to the BHs of our universe
- **soft hair** (HPS 2016) — BMS degrees of freedom on the BH surface and information encoding

**Related concepts and deepening**:
- the Page average, generic states, Haar randomness
- generalized entropy, the second law of black-hole thermodynamics
- bulk reconstruction, code subspace, HaPPY applications
- the factorization paradox, ensemble vs single CFT
- exact analysis of AdS$_2$/SYK, the Schwarzian action
- the details of the firewall-avoidance mechanism of ER=EPR
- other scenarios of the information paradox (one-sided BH, eternal BH, dynamical BH)

**arXiv and literature**:
- **Page 1993a** — *Information in black hole radiation*, PRL 71, 3743 (arXiv:hep-th/9306083)
- **Page 1993b** — *Average entropy of a subsystem*, PRL 71, 1291
- AMPS 2012 — arXiv:1207.3123
- Engelhardt-Wall 2014 — arXiv:1408.3203 (QES)
- **Penington 2019** — arXiv:1905.08255
- **AEMM 2019** — arXiv:1905.08762
- Saad-Shenker-Stanford 2019 — arXiv:1903.11115 (JT + SYK)
- **PSSY 2020** — arXiv:1911.11977 (Replica wormholes)
- **AHMS 2020** — arXiv:1911.12333 (independent derivation)
- **Almheiri-Hartman-Maldacena-Shaghoulian-Tajdini 2021** — *The entropy of Hawking radiation*, RMP — arXiv:2006.06872 (the comprehensive review)
- Penington 2021 — *The Black Hole Information Problem: Past, Present, and Future*

## Workflow Connections
- index
- physicist
- modern-README
- Hawking radiation
- AdS/CFT
- Quantum Information
