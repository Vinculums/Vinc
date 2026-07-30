# Replica wormholes

## One-line essence
Replica wormholes are the wormhole-topology saddles that appear when the von Neumann entropy of Hawking radiation is computed directly by the Euclidean quantum-gravity path integral — exactly analyzed in the JT gravity + SYK toy models, they refine the microscopic mechanism of the Page curve island formula, show that **the topological sum of the quantum-gravity partition function is the home ground of information recovery**, and create a new unsolved problem called the factorization paradox.

## Physical picture

**The quantum-gravity application of the replica trick**:
The quantitative derivation tool for the von Neumann entropy seen in Quantum Information and Page curve:
$$S_R = -\lim_{n \to 1}\frac{1}{n-1}\ln \text{Tr}\rho_R^n$$
Compute $\text{Tr}\rho_R^n$ by the quantum-gravity path integral. An integral over a manifold $\Sigma_n$ where **$n$ copies of spacetime** are connected by boundary cyclic branching.

**The competition of two saddles** (the saddle points of the Euclidean quantum-gravity partition function):
1. **Disconnected**: the $n$ copies are independent. Each copy has only its own boundary. $Z_{\rm disc}^{(n)} = (Z_1)^n$ → the naive Hawking result (monotonic increase)
2. **Connected (replica wormhole)**: the $n$ copies are connected by wormholes. The handles among the copies contribute a new topology → the island-formula result (Page curve)

**Page time = the saddle-dominance switching point**: the moment the actions of the two saddles become equal. Before it, disconnected (monotonic Hawking); after it, wormhole (the decreasing part of the Page curve).

**Analogy — the quantum interference of two paths**: in optics, when two paths combine there is interference. In quantum gravity, the two topologies (disconnected and wormhole) are summed in the partition function. **What Hawking 1976 saw was only the disconnected.** When the wormhole is added, information is recovered.

**JT gravity + SYK — the stage of exact analysis**:
- **JT gravity** (Jackiw 1985, Teitelboim 1983): 1+1-dimensional dilaton gravity. A 2D AdS background + the boundary Schwarzian action. An **exactly solvable** quantum gravity (equivalent to random-matrix theory, Saad-Shenker-Stanford 2019)
- **SYK model** (Sachdev-Ye 1993, Kitaev 2015): random four-body interactions of $N$ Majorana fermions. Conformal symmetry at low energy, **the boundary dual of JT**
- combined → the exact AdS$_2$/CFT$_1$ correspondence. The deepest meeting of quantum gravity + quantum information + condensed matter

**The topological meaning of the replica wormhole**:
2 copies + 1 wormhole = a JT disk + 2 anchors + 1 handle. **Not a physically real spacetime bridge, but a non-perturbative contribution to the quantum-gravity partition function.** Different from ER=EPR — ER=EPR is Lorentzian, the replica wormhole is Euclidean.

**Reinterpretation of Hawking 1976**: the monotonic increase is a **first approximation** (the disconnected saddle). The quantum correction = the wormhole saddle. Combining the two gives the Page curve. **Hawking was not wrong; he saw only a part of the full quantum-gravity partition function.**

**Factorization paradox** (2022–):
If the boundary CFT is a single quantum system, the partition function must factorize into a product. Yet the wormhole connects the copies → factorization violation. Two interpretations:
- **Ensemble**: the boundary is an ensemble of CFTs (like a random matrix). The wormhole = natural in the ensemble average
- **Single CFT**: if the boundary is a single CFT, where does the wormhole come from? Unsolved

This paradox is a new question about the deep structure of quantum gravity — the success of replica wormholes created a new unsolved problem.

## Mathematical structure

**JT gravity action** (1+1 dimensions, 2D dilaton gravity):
$$S_{\rm JT} = -\frac{S_0}{4\pi}\int d^2x\sqrt{-g}\, R - \frac{1}{16\pi G}\int d^2x\sqrt{-g}\,\phi(R + 2/L^2) - \frac{1}{8\pi G}\int_{\partial}du\sqrt{h}\,\phi(K - 1)$$
- $\phi$: the dilaton field
- $S_0$: the topological Euler term, the coefficient of the genus expansion
- $L$: the AdS$_2$ radius, $K$: the extrinsic curvature
- the bulk term is linear in $\phi$ → **a computable quantum gravity**

**Boundary Schwarzian action** (the low-energy limit of JT):
$$S_{\rm Schw} = -C\int du\, \{f(u), u\}$$
- $\{f, u\} = f'''/f' - \tfrac{3}{2}(f''/f')^2$: the Schwarzian derivative
- $C$: the coefficient of the dilaton boundary condition
- SL(2,$\mathbb R$) gauge symmetry

**SYK model**:
$$H_{\rm SYK} = i^{q/2}\sum_{1 \leq i_1 < \cdots < i_q \leq N} J_{i_1\cdots i_q}\chi_{i_1}\cdots\chi_{i_q}$$
- $\chi_i$: $N$ Majorana fermions, $\{\chi_i, \chi_j\} = \delta_{ij}$
- $J_{i_1\cdots i_q}$: random couplings, $\langle J^2\rangle = J^2(q-1)!/N^{q-1}$
- the low-energy limit ($\beta J \gg 1$): conformal symmetry + Schwarzian breaking → the boundary dual of JT

**The Maldacena-Stanford duality (2016)**:
$$\text{SYK low energy} \;\equiv\; \text{AdS}_2 \text{ JT gravity}$$
Matches the JT partition function in the SYK $N \to \infty$ limit.

**JT partition function — the genus expansion** (Saad-Shenker-Stanford 2019):
$$Z_{\rm JT}(\beta) = \sum_{g=0}^{\infty} e^{-(2g-1) S_0}\,Z_g(\beta)$$
- $g$: the genus (number of handles)
- $Z_0$: the disk (a single boundary)
- $Z_g$ ($g \geq 1$): the wormhole-topology contributions

**Random-matrix equivalence**:
The JT-gravity partition function = the partition function of a double-scaled random-matrix theory
$$Z_{\rm JT}(\beta_1, \cdots, \beta_n) = \int dM\, e^{-N\,\text{tr}\,V(M)}\prod_i \text{tr}\,e^{-\beta_i M}$$

**Replica trick — two boundaries**:
$$\text{Tr}\,\rho_R^n = \frac{Z_n}{(Z_1)^n}$$
- $Z_n$: $n$ copies + the sum over all topologies
- $(Z_1)^n$: only the $n$ disconnected copies

**Disconnected saddle**:
$$Z_n^{\rm disc} = (Z_1)^n \;\Rightarrow\; \text{Tr}\,\rho_R^n\bigg|_{\rm disc} = 1$$
$\Rightarrow S_R^{\rm disc} = 0$ (a wrong result — but another interpretation corresponding to the monotonic increase of the Hawking scenario)

**Wormhole saddle** (n=2, the double trumpet):
$$Z_2^{\rm worm} \sim \int_0^\infty db\,b\,Z_{\rm trumpet}(\beta_1, b)\,Z_{\rm trumpet}(\beta_2, b)$$
- $b$: the throat length of the wormhole
- the two trumpets connected by a cylinder of length $b$

**Derivation of the island formula** (via the replica wormhole):
The $n \to 1$ limit + the saddle-dominance analysis → exactly reproducing the island formula of the Page curve
$$S_R = \min_I \text{ext}\left[\frac{A(\partial I)}{4G_N} + S_{\rm bulk}(R \cup I)\right]$$

**Exact analysis in the TFD state**:
$$|TFD\rangle = \frac{1}{\sqrt Z}\sum_n e^{-\beta E_n/2}|n\rangle_L|n\rangle_R$$
The von Neumann entropy of one of the two boundaries is exactly the Page curve.

**SYK OTOC** (out-of-time-order correlator) — quantum chaos:
$$\text{OTOC}(t) \sim 1 - \frac{1}{N}e^{\lambda_L t}$$
- $\lambda_L = 2\pi/\beta$: the Lyapunov exponent, **exactly saturating the MSS bound** (Maldacena-Shenker-Stanford 2015)
- SYK = a "fast scrambler," the same timescale as the quantum chaos of a BH

## Why it matters

**Refining the microscopic mechanism of the Page curve**:
- 18: the island formula reproduces the Page curve, but why was unknown
- 19: the wormhole-topology saddle of the Euclidean quantum-gravity partition function is the epicenter of information recovery. **Quantum gravity = a sum over wormholes**

**A nearly complete solution of the information paradox in 1+1 dimensions**:
- JT + SYK is exactly solvable
- every stage of the Page curve is exactly derived
- it is exactly verified that the naive Hawking is the disconnected saddle and the quantum correction is the wormhole saddle

**The deepest meeting of quantum gravity + quantum information + condensed matter**:
- JT (quantum gravity) ↔ SYK (condensed matter) ↔ the information paradox (quantum information)
- three fields united in one toy model — one of the deepest integration points at the Modern stage

**The appearance of random-matrix theory**:
- the JT partition function = exactly the same as random-matrix theory
- quantum gravity as one face of statistical physics (random matrices)
- suggesting **the ensemble interpretation of quantum gravity**

**Factorization paradox — a new unsolved area**:
- the success of replica wormholes raises a problem for the single-CFT interpretation
- "is quantum gravity an ensemble, or a single system?"
- a question about the deep structure of string theory and M-theory
- 2022– the most active unsolved problem of quantum gravity

**Technical value**:
- a standard tool for non-perturbative computation of quantum-gravity partition functions
- the genus expansion = the generalization of string-theoretic wormhole surfaces
- a quantitative tool for the BH interior spacetime (Stanford-Yang 2021)

**Unsolved**:
- **higher-dimensional generalization** — 4D AdS is partial; flat space and dS are unsolved
- **resolving the factorization paradox** — the origin of the wormhole in a single CFT
- **the Lorentzian interpretation** — is the replica wormhole a real spacetime bridge?
- **a clear picture of the black-hole interior spacetime** (firewall vs smooth vs fuzzball)
- **cosmological application** — replica wormholes of dS/CFT

## Historical context

**Act I — the replica trick and Euclidean quantum gravity (1980s–2014)**:
- 1985 Jackiw / 1983 Teitelboim — the first proposal of 2D dilaton gravity (JT). Initially dismissed as a toy model
- 1993 Sachdev-Ye — an SYK-like model (condensed matter)
- 2007 Casini-Huerta — a replica-trick derivation of the RT formula
- 2013 Faulkner-Lewkowycz-Maldacena (FLM) — RT quantum corrections
- **2014 Engelhardt-Wall** — the Quantum Extremal Surface (QES). The quantum generalization of RT
- **The decisive turn**: 2014 QES — the appearance of the combined tool of the replica trick + quantum corrections + the extremization prescription. The following 5 years accumulate

**Act II — the JT + SYK duality takes hold (2015–2018)**:
- **2015 Kitaev** — rediscovery of the SYK model, fast scrambler and MSS-bound saturation
- **2015 Maldacena-Shenker-Stanford** — the quantum bound on the Lyapunov exponent (the MSS bound)
- **2016 Maldacena-Stanford** — *Remarks on the Sachdev-Ye-Kitaev model* (arXiv:1604.07818). The formal duality: SYK low energy = JT gravity
- 2017– exact analysis of the JT partition function (Stanford, Witten, et al.)
- 2017 the formal Engelhardt-Wall QES
- **The decisive turn**: 2016 — JT + SYK rises as the exactly analyzable toy model of quantum gravity. Quantum gravity, previously abstract, can be solved by hand in 1+1

**Act III — the appearance of replica wormholes (2019)**:
- **March 2019 Saad-Shenker-Stanford** (arXiv:1903.11115) — *JT gravity as a matrix integral*. The wormhole contribution to the JT partition function + the random-matrix equivalence
- **May 2019 Penington / AEMM** — the formal island formula (see Page curve)
- **November 2019 PSSY** (Penington-Shenker-Stanford-Yang, arXiv:1911.11977) — *Replica wormholes and the black hole interior*. The formal derivation of the replica wormhole in JT
- **November 2019 AHMS** (Almheiri-Hartman-Maldacena-Shaghoulian-Tajdini, arXiv:1911.12333) — *Replica Wormholes and the Entropy of Hawking Radiation*. Independent derivation, making explicit the wormhole saddle of Euclidean quantum gravity
- **The decisive turn**: November 2019 — PSSY and AHMS independently derive replica wormholes formally in the same month. A nearly complete solution of the microscopic mechanism of Hawking's 1976 information paradox. Quantum gravity climbs one more step as a **computable discipline**

**Act IV — generalization, ramifications, and the factorization paradox (2020–)**:
- 2020 Almheiri et al. — 4D replica wormholes
- 2020 various JT variants (matter coupling, supersymmetric)
- 2021 **Stanford-Yang** — *Black hole interior portal*. The replica wormhole directly affects the picture of the BH interior spacetime
- 2021 Almheiri-Hartman-Maldacena-Shaghoulian-Tajdini — *The entropy of Hawking radiation* (RMP, arXiv:2006.06872), the comprehensive review
- **2022 Saad-Shenker-Stanford et al. — the factorization paradox in earnest**: ensemble vs single CFT unsolved
- 2023– attempts to generalize to flat space and dS (mostly unsolved)
- 2024 — replica wormholes settled as a standard tool of quantum gravity; the factorization paradox + flat/dS generalization the most active unsolved problems
- **The decisive turn**: 2022– the factorization paradox — the success of replica wormholes creates a new unsolved problem. "Is quantum gravity an ensemble or single?" — re-asking the deep-structure question of quantum gravity

**Key insight**: replica wormholes showed that **the topological sum of the quantum-gravity partition function is the home ground of information recovery.** What Hawking 1976 saw was only the disconnected saddle — not wrong in itself but **only a part of the whole of quantum gravity.** The 1985 JT proposal → the 2015 SYK rediscovery → the 2016 duality → the 2019 wormhole: **34 years of accumulation** combined into a nearly complete solution of the 1+1-dimensional information paradox. Yet that success created the new unsolved factorization paradox, reopening the deep-structure question of quantum gravity — the essential pattern of quantum gravity in which **a solution gives birth to a new question.**

## Connections

**Prior connections** (already studied):
- Page curve — mentioned as a toy model in 18, exactly analyzed in 19. **A direct bridge** (this note is the microscopic-mechanism refinement of 18)
- Hawking radiation — the formal definition of the island formula is a result of replica wormholes
- AdS/CFT — the replica derivation of RT, the Euclidean quantum-gravity dictionary, the exact AdS$_2$/CFT$_1$ correspondence
- Quantum Information — the von Neumann entropy, the replica trick
- Quantum Field Theory — the Euclidean path integral, the partition function, the sum over topologies
- General Relativity — JT = 1+1 dilaton gravity, the Schwarzschild and Kerr BHs
- Statistical Mechanics — the partition function, the ensemble average, random-matrix theory
- Condensed Matter — **the SYK model = the condensed-matter + quantum-gravity bridge** (Sachdev-Ye 1993, begun in condensed matter)
- Quantum Mechanics — unitarity, the reduced density matrix, the tensor product

**Next connections** (full quantum-gravity follow-ups):
- **the string-theory landscape + Strominger-Vafa BH counting** (new P2) — string-theoretic wormhole surfaces, the $10^{500}$ vacua
- **the factorization paradox** (new P2, the most unsolved) — ensemble vs single CFT
- **generalization to flat and dS BHs** (new P2, unsolved)
- **soft hair** (HPS 2016)
- LQG·CDT alternatives (P3)

**Related concepts and deepening**:
- the exact AdS$_2$/CFT$_1$ correspondence (Schwarzian = SYK low energy)
- random-matrix theory (GOE, GUE, equivalent to the JT partition function)
- the Maldacena-Shenker-Stanford quantum-chaos bound (Lyapunov $\leq 2\pi T/\hbar$)
- black-hole interior, complexity, scrambling, butterfly velocity
- BFSS·BMN matrix models (a non-perturbative definition of M-theory)
- the string-theory genus expansion, world-sheet topology
- topological recursion, the Witten conjecture, Eynard-Orantin
- ETH (Eigenstate Thermalization Hypothesis), quantum ergodicity

**arXiv and literature**:
- Jackiw 1985, Teitelboim 1983 — the origin of JT gravity
- Kitaev 2015 — KITP talks, the rediscovery of the SYK model
- Maldacena-Shenker-Stanford 2015 — *A bound on chaos*, JHEP — arXiv:1503.01409
- **Maldacena-Stanford 2016** — *Remarks on the SYK model*, arXiv:1604.07818
- **Saad-Shenker-Stanford 2019** — *JT gravity as a matrix integral*, arXiv:1903.11115
- **PSSY 2019** — *Replica wormholes and the black hole interior*, arXiv:1911.11977
- **AHMS 2019** — *Replica Wormholes and the Entropy of Hawking Radiation*, arXiv:1911.12333
- Stanford-Yang 2021 — *Finite-cutoff JT gravity and self-avoiding loops*, arXiv:2004.08005
- Almheiri et al. 2021 — *The entropy of Hawking radiation*, RMP — arXiv:2006.06872 (the comprehensive review)
- Penington 2021 — *The Black Hole Information Problem: Past, Present, and Future*

## Workflow Connections
- index
- physicist
- modern-README
- Page curve
- Hawking radiation
- AdS/CFT
- Condensed Matter
