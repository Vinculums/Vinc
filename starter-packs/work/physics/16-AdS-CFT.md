# AdS/CFT

## One-line essence
The AdS/CFT correspondence is the statement that the quantum gravity of a $(d+1)$-dimensional anti-de Sitter space is exactly equivalent to the $d$-dimensional conformal field theory (CFT) on its boundary — the most precise realization of the holographic principle that quantum gravity is solved by a quantum field theory of fewer dimensions, the home ground of the RT formula and ER=EPR registered in Condensed Matter and Quantum Information, and the formal bridge toward the microscopic mechanism of General Relativity's Hawking radiation.

## Physical picture

**The starting point — the two faces of the string-theory D-brane (Maldacena 1997)**:
$N$ D3-branes admit two descriptions:
1. **Open-string description**: 4D $N=4$ supersymmetric Yang-Mills (SYM) gauge theory on the D-branes
2. **Closed-string description**: the spacetime curvature made by the D-branes = AdS$_5$ × S$^5$ quantum gravity

Two descriptions of the same physical system → **the two theories are exactly equivalent.** This is the first statement of AdS/CFT.

**The holographic principle**:
't Hooft 1993, Susskind 1995. Black-hole entropy is proportional not to volume but to **area** → the information of $D$-dimensional quantum gravity is **encoded** on the $D-1$-dimensional boundary. AdS/CFT is the most precise realization of this principle — not a mere hypothesis but a **mathematically exact equivalence.**

**Analogy — a hologram image**:
A hologram is a form in which 3D information is encoded on a 2D film. In AdS/CFT, **3D quantum gravity** is encoded in a **2D gauge theory** (actually $d+1 \leftrightarrow d$). But the analogy goes only that far — AdS/CFT is a genuine equivalence, and for every bulk event there is an exactly corresponding boundary event.

**The dictionary** — mapping the two languages:
- a free scalar field in the bulk ↔ a single-trace operator on the boundary
- bulk mass $m$ ↔ boundary dimension $\Delta$: $\Delta(\Delta - d) = m^2 R^2$
- the bulk gravitational isometry $SO(d, 2)$ ↔ the boundary conformal symmetry $SO(d, 2)$ (exactly the same group!)
- a bulk black hole ↔ a boundary finite-temperature state
- bulk two BHs + wormhole ↔ the thermofield double state of two boundary CFTs

**Strong-coupling ↔ weak-coupling duality** (the most powerful application):
When the 't Hooft coupling $\lambda = g^2 N$ is large (strongly coupled gauge theory, hard to solve directly) the bulk is weakly coupled classical gravity (easy to solve). **The reverse also holds.** Condensed-matter strong correlation, nuclear-physics quark-gluon plasma, high-temperature superconductivity, and so on — **problems that could not be solved are solved by gravitational calculation.** This is the home ground of holographic condensed matter.

**Ryu-Takayanagi formula** (registered in Quantum Information, the home ground is here):
The entanglement entropy of a boundary CFT region $A$ = the homology-equivalent minimal area in the bulk / $4G\hbar$. **Entanglement is area** — the boundary-region generalization of the Bekenstein-Hawking $S_{BH} = A/(4G\hbar)$.

**ER=EPR** (Maldacena-Susskind 2013):
The EPR entanglement between two qubits ↔ the ER wormhole between two black holes. Exactly the same physics. **Quantum information is a spacetime bridge** — the possibility that spacetime emerges from entanglement. Made into a slogan in Quantum Information ("It from Qubit").

**HaPPY code** (Pastawski-Yoshida-Harlow-Preskill 2015):
The bulk reconstruction of holography has the same structure as a **quantum error-correcting code.** Even knowing only part of the boundary region, one can restore the deep interior of the bulk — the redundancy of quantum error correction. The implication that **spacetime itself is a quantum code.**

**Page curve and the island formula** (Penington 2019, Almheiri et al. 2019):
A quantum-information + AdS/CFT partial solution to Hawking's 1976 information paradox (is information lost when a black hole evaporates?). With the RT formula + quantum corrections, the **Page curve** (the information-recovery curve of black-hole evaporation) is derived. **A partial solution of a 50-year unsolved problem.** It is quantitatively shown that Hawking radiation comes out carrying information.

## Mathematical structure

**AdS$_{d+1}$ metric** (Poincaré coordinates):
$$ds^2 = \frac{R^2}{z^2}\left(-dt^2 + d\vec x^2 + dz^2\right)$$
- $z$: the radial direction ($z \to 0$ is the boundary, $z \to \infty$ is the deep bulk)
- $R$: the AdS radius

**The conformal symmetry of the boundary $z \to 0$**:
The $d$-dimensional conformal group $SO(d, 2)$ — exactly the same as the AdS$_{d+1}$ isometry group. This equivalence binds the two theories.

**GKP-Witten relation** (1998, the exact formulation of the dictionary):
$$Z_{\text{bulk}}[\phi_0] = \left\langle \exp\left(\int d^d x\, \phi_0(\vec x)\, \mathcal O(\vec x)\right)\right\rangle_{\text{CFT}}$$
- left side: the bulk quantum-gravity partition function (boundary condition $\phi(\vec x, z=0) = \phi_0$)
- right side: the boundary CFT generating function (source $\phi_0$)
- **the bulk action = the boundary effective action**

**Mass-dimension relation**:
A scalar field of bulk mass $m$ ↔ the conformal dimension $\Delta$ of a boundary single-trace operator $\mathcal O$:
$$\Delta(\Delta - d) = m^2 R^2 \;\Rightarrow\; \Delta_\pm = \frac{d}{2} \pm \sqrt{\left(\frac{d}{2}\right)^2 + m^2 R^2}$$
A massless vector field ($A_\mu$) ↔ a conserved current ($J^\mu$, $\Delta = d-1$).
A spin-2 graviton ↔ the stress tensor ($T^{\mu\nu}$, $\Delta = d$).

**The prototypical example — AdS$_5$ × S$^5$ ↔ $\mathcal N = 4$ SYM**:
- bulk: 10D type IIB string theory on AdS$_5$ × S$^5$
- boundary: 4D $\mathcal N = 4$ supersymmetric Yang-Mills, gauge group $SU(N)$
- 't Hooft coupling $\lambda = g_{YM}^2 N$
- 't Hooft limit: $N \to \infty$ with $\lambda$ fixed → classical strings in the bulk
- **strong-weak duality**:
$$\lambda \gg 1: \text{classical gravity (bulk easy to solve)}, \quad \lambda \ll 1: \text{perturbative gauge (boundary easy to solve)}$$

**Ryu-Takayanagi formula**:
$$S_A^{\text{CFT}} = \frac{\text{Area}(\gamma_A)}{4 G_N \hbar}$$
- $\gamma_A$: the homology-equivalent **minimal area** of the boundary region $A$ (a surface reaching into the depth of the bulk spacetime)
- $G_N$: the bulk Newton constant
- $A$ is the whole boundary → $\gamma_A$ is the BH horizon → $S = A_H/(4G)$ = Bekenstein-Hawking

**HRT generalization** (time-dependent):
Generalizes $\gamma_A$ to an **extremal surface** — Hubeny-Rangamani-Takayanagi 2007.

**Quantum corrections** (Faulkner-Lewkowycz-Maldacena 2013):
$$S_A = \frac{\text{Area}(\gamma_A)}{4G_N} + S_{\text{bulk}}^{\text{ent}}(\Sigma_A)$$
$\Sigma_A$: the bulk region enclosed by $\gamma_A$. The quantum correction is the entanglement entropy of the bulk quantum fields.

**Quantum Extremal Surface (QES, Engelhardt-Wall 2017)**:
Extremize the sum of area + quantum correction. The foundation of the island formula.

**Island formula** (Penington 2019, Almheiri et al. 2019):
$$S_R = \min\,\text{ext}\left[\frac{\text{Area}(\partial I)}{4G_N} + S_{\text{bulk}}(R \cup I)\right]$$
- $R$: the region where Hawking radiation is collected (outside the black hole)
- $I$: the island — a region inside the black hole but holding the information of $R$
- result: derivation of the **Page curve.** It is shown that Hawking radiation comes out carrying information

**ER=EPR — Thermofield Double**:
$$|TFD\rangle = \frac{1}{\sqrt Z}\sum_n e^{-\beta E_n/2}|n\rangle_L \otimes |n\rangle_R$$
The maximally entangled state of two boundary CFTs = the eternal BH of the bulk (this side and that side connected by a wormhole, no information transfer possible). **Two-qubit entanglement ↔ a wormhole between two BHs.**

**KSS viscosity bound** (Kovtun-Son-Starinets 2005):
$$\frac{\eta}{s} \geq \frac{\hbar}{4\pi k_B}$$
- $\eta$: shear viscosity, $s$: entropy density
- a universal bound of all natural liquids. Exactly derived in the holographic calculation
- the measured value of the quark-gluon plasma (RHIC) is very close to the bound — **nature is a nearly perfect liquid**

## Why it matters

**A precise definition of quantum gravity**:
Quantum gravity is non-perturbative and non-renormalizable → cannot be solved directly. AdS/CFT **exactly defines** quantum gravity by the boundary gauge theory (which is well defined). The first precise definition of non-perturbative quantum gravity.

**A partial solution of the black-hole information paradox**:
- Hawking 1976: is information lost when a BH evaporates? — 50 years unsolved
- Penington / Almheiri 2019: derivation of the Page curve via AdS/CFT + RT + the island formula
- it is quantitatively shown that **Hawking radiation comes out carrying information**
- but the generalization to flat spacetime and dS is unsolved

**Strong-correlation tools for Condensed Matter**:
- high-temperature superconductivity (holographic superconductivity)
- strange metal — not solvable by an ordinary Fermi liquid → AdS/CFT is a candidate
- the viscosity bound (verified at RHIC)
- a general theory of non-Fermi liquids — active via Sachdev et al.

**Quantum Information and spacetime**:
- RT: entanglement entropy = area
- ER=EPR: entanglement = wormhole
- HaPPY: spacetime = a quantum error-correcting code
- Implication: **the possibility that spacetime emerges from entanglement** — the most active branch of quantum gravity

**A bridge to Cosmology and General Relativity**:
- dS/CFT — the de Sitter version, an attempt to apply it to our universe. Unsolved but active
- the microscopic mechanism of Hawking radiation → can be refined by the AdS/CFT dictionary (the next P1 area)
- generalization of cosmic-horizon entropy

**Technical value**:
- non-perturbative results of gauge theory via gravitational calculation (previously only lattice was possible)
- a standard tool for strongly coupled systems in general
- JT gravity + SYK model — exact analysis in 1+1 dimensions

**Unsolved**:
- does every gravitational theory have a holographic dual?
- the exact form of dS/CFT? (application to our universe)
- quantum gravity in flat space (the S-matrix program)
- is AdS/CFT limited to string theory, or is it general quantum gravity?
- background independence (the emergence of spacetime itself)

## Historical context

**Act I — the emergence of the holographic principle (1993–1997)**:
- 1972 Bekenstein — BH entropy = area / 4 (the first quantitative clue of quantum gravity)
- 1974 Hawking — Hawking radiation + the information paradox (1976)
- **1993 't Hooft** — *Dimensional Reduction in Quantum Gravity* (arXiv:gr-qc/9310026). The dimensional-reduction hypothesis from area proportionality
- **1995 Susskind** — *The World as a Hologram* (arXiv:hep-th/9409089). Formulation of the holographic principle
- 1995 Polchinski — D-branes as the non-perturbative degrees of freedom of string theory (a watershed)
- 1996– Strominger-Vafa BH microstate counting (via D-branes)
- **1997 November Maldacena** — *The large N limit of superconformal field theories and supergravity* (arXiv:hep-th/9711200). The formal statement AdS₅×S⁵ ≡ 4D $\mathcal N=4$ SYM
- **The decisive turn**: 1997 Maldacena — holography goes from conjecture to an **exact mathematical equivalence.** The watershed of modern theoretical physics

**Act II — establishment of the dictionary and an explosion of applications (1998–2005)**:
- 1998 GKP (Gubser-Klebanov-Polyakov) + Witten — the bulk/boundary dictionary. Generating function = boundary action
- 1998 Witten — *Anti-de Sitter space and holography* (arXiv:hep-th/9802150)
- 1998 BTZ → AdS$_3$/CFT$_2$, exact analysis possible
- 2000 Aharony-Gubser-Maldacena-Ooguri-Oz — the standard review (arXiv:hep-th/9905111)
- **2001 Maldacena Eternal BH = TFD** (arXiv:hep-th/0106112) — the seed of ER=EPR
- 2002 Karch-Randall — localized AdS/CFT, brane-world
- **2005 KSS viscosity bound** (Kovtun-Son-Starinets) — $\eta/s \geq \hbar/(4\pi k_B)$. **The start of holographic condensed matter**
- **The decisive turn**: 2005 KSS — AdS/CFT goes from a string-theory curiosity to an **industrial tool for condensed matter and nuclear physics.** The RHIC quark-gluon plasma is measured to almost reach the bound

**Act III — the quantum-information age (2006–2018)**:
- **2006 Ryu-Takayanagi** (arXiv:hep-th/0603001) — entanglement entropy = minimal area
- 2007 HRT — the time-dependent generalization
- 2009 Casini-Huerta-Myers — the first mathematical derivation of the RT formula
- 2013 **Maldacena-Susskind ER=EPR** (arXiv:1306.0533) — entanglement = wormhole
- 2013 Faulkner-Lewkowycz-Maldacena — RT quantum corrections
- 2014 Almheiri-Dong-Harlow — bulk reconstruction
- **2015 HaPPY code** (Pastawski-Yoshida-Harlow-Preskill, arXiv:1503.06237) — holography = quantum error correction
- 2016 Maldacena-Stanford SYK — an exactly analyzable 1+1 AdS/CFT model
- 2017 Engelhardt-Wall QES — Quantum Extremal Surface
- **The decisive turn**: 2013 ER=EPR — AdS/CFT becomes the formal language of quantum information. The **It from Qubit** movement begins (2015–)

**Act IV — a partial solution of the information paradox and generalization (2019–)**:
- **2019 Penington** (arXiv:1905.08255) / **2019 Almheiri-Engelhardt-Marolf-Maxfield** — derivation of the Page curve, the **island formula**
- 2020 replica wormholes (AEMM, PSSY) — the exact information-recovery mechanism
- 2020s — the 50-year-unsolved information paradox begins to be partially solved
- 2020s attempts to generalize quantum gravity to flat space, dS, and non-AdS (mostly unsolved)
- 2024 — AdS/CFT as the most active branch of quantum gravity. Application to our universe still unsolved
- **The decisive turn**: 2019 the island formula — Hawking's 1976 50-year-unsolved problem gets a partial solution via quantum information + AdS/CFT. Quantum gravity goes from conjecture to a **computable discipline**

**Key insight**: AdS/CFT **transformed three times.** First (1997) as a precise statement of string theory, second (2005–) as a tool for condensed matter and nuclear physics, third (2013–) as the formal language of quantum information and black-hole information. The last transformation is the deepest — the **quantum information ↔ quantum gravity equivalence** quantitatively suggests the possibility that spacetime emerges from entanglement. Before Maldacena 1997 quantum gravity was conjecture. After it, quantum gravity became a **computable discipline** (at least in the AdS domain).

## Connections

**Prior connections** (already studied):
- Quantum Information — **the home ground of RT, ER=EPR, HaPPY, and the Page curve.** The quantum-information tools registered in 15 are refined into a formal language in 16
- Quantum Field Theory — boundary CFT, gauge theory, the 't Hooft limit ($N\to\infty$). The depth of conformal symmetry
- General Relativity — bulk quantum gravity, the AdS metric, BH thermodynamics. Hawking radiation and the information paradox
- Condensed Matter — strong correlation, the viscosity bound, and high-temperature superconductivity as direct applications (holographic condensed matter)
- Cosmology — dS/CFT application attempts, the cosmic horizon
- Statistical Mechanics — Bekenstein-Hawking + partition function, TFD
- Quantum Mechanics — entanglement, tensor product, and Bell states as the quantum-information tools of RT and ER=EPR
- Quantum Mechanics II — tensor networks, quantum error correction

**Next connections** (unsolved and advanced):
- **the microscopic mechanism of Hawking radiation** (the first priority of the next P1 supplement) — refining the Bogoliubov transformation, adiabatic vacuum, and Unruh effect via the AdS/CFT dictionary
- **full quantum gravity** — the details of the Page-curve derivation, application of the island formula, the string-theory landscape
- **dS/CFT** — application to our universe unsolved
- **JT gravity + SYK** — exact analysis in 1+1 dimensions
- **topological QFT (TQFT)** — a common tool of 13·15·16

**Related concepts and deepening**:
- the 't Hooft limit, large-$N$ expansion, planar diagrams
- $\mathcal N = 4$ SYM, BPS states, protected operators
- AdS$_3$/CFT$_2$ — the Brown-Henneaux central charge
- Witten diagrams, the conformal bootstrap
- the general form of gauge-gravity duality (M-theory, F-theory)
- bulk reconstruction, subregion duality, code subspace
- quantum chaos, scrambling, OTOC, butterfly velocity
- BFSS·BMN matrix models (non-perturbative M-theory)
- the exact solution of JT (Jackiw-Teitelboim) gravity

**arXiv and literature**:
- **Maldacena 1997** — arXiv:hep-th/9711200 (the watershed paper)
- **Witten 1998** — arXiv:hep-th/9802150 (formulation of the dictionary)
- Aharony-Gubser-Maldacena-Ooguri-Oz 2000 — arXiv:hep-th/9905111 (the standard review)
- **Ryu-Takayanagi 2006** — arXiv:hep-th/0603001
- **Maldacena-Susskind 2013** — arXiv:1306.0533 (ER=EPR)
- **Pastawski-Yoshida-Harlow-Preskill 2015** — arXiv:1503.06237 (HaPPY)
- **Penington 2019** — arXiv:1905.08255 (Page curve)
- **Almheiri-Engelhardt-Marolf-Maxfield 2019** — arXiv:1905.08762 (the island formula)
- textbooks: Nastase, *Introduction to AdS/CFT*; Ammon-Erdmenger, *Gauge/Gravity Duality*; Polchinski *String Theory* vol II

## Workflow Connections
- index
- physicist
- modern-README
- Quantum Information
- General Relativity
