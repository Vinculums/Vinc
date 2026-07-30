# Swampland

## One-line essence
As a direct response to the verifiability crisis of the String Theory landscape ($10^{500}$ vacua), the classification program formalized by **Vafa 2005** — it separates the swampland, "EFTs that look consistent but cannot be completed into string theory," from the landscape, and its six conjectures ① **No global symmetries** ② **Distance conjecture** (Ooguri-Vafa 2006) ③ **Weak Gravity Conjecture** (Arkani-Hamed-Motl-Nicolis-Vafa 2007) ④ **dS conjecture** (OOSV 2018) ⑤ **TCC** (Bedroya-Vafa 2019) ⑥ **Cobordism conjecture** (McNamara-Vafa 2019) formalize the *constraints* of the general principles of quantum gravity. It raises **the possibility that the dS conjecture denies the entry of our universe (accelerating expansion) into the landscape** — a deep motivation shared with the dS BH Susskind dS censorship. **The seventh note of the full quantum-gravity domain, formalizing the *constraint principles* of quantum gravity following the 22 formal tools and the 23 application to our universe**.

## Physical picture

**Why the Swampland is needed**:
- The String Theory landscape = $10^{500}$ vacua → if "every EFT can be completed into string theory," it is unverifiable (no prediction)
- But *not every* EFT enters the landscape — a **swampland** exists
- swampland classification = "which EFTs do not agree with string-theory consistency" → constraint principles can be derived
- "**The Landscape, the Swampland, and the Multiverse**" (Vafa 2005)

**Analogy — two sets**:
- **Landscape**: the set of string-theory-consistent EFTs (estimated $10^{500}$)
- **Swampland**: the set of EFTs that look consistent but cannot be completed into string theory (potentially larger)
- the boundary between the two is the general principle of quantum gravity = the swampland conjectures

**Six conjectures**:

### ① No global symmetries
**Principle**: no exact global symmetry in quantum gravity. Every global symmetry is either (a) gauged or (b) broken by non-perturbative effects.

**Evidence**:
- BH evaporation — if a global charge $Q$ falls into a BH, the Hawking radiation is indifferent (no-hair) → not conserved
- AdS/CFT — every symmetry of the boundary CFT is gauged (no-hair theorem)
- Banks-Seiberg 2010, **Harlow-Ooguri 2018** (formal proof in CFT dimension)

**Meaning**: global baryon number / lepton number are also *approximate* symmetries. Proton decay / neutrino mass arise naturally.

### ② Distance conjecture (Ooguri-Vafa 2006)
**Principle**: at the infinite-distance limit $\phi \to \infty$ of the moduli space $\mathcal M$, **an infinite tower of light states appears**.

**Formalization**:
$$m(\phi) \sim m_0 e^{-\alpha \cdot d(\phi_0, \phi)/M_{\rm pl}}$$
- $d(\phi_0, \phi)$: moduli distance
- $m \to 0$ exponential decay as distance → ∞

**Meaning**: the EFT breaks at the limit point — new degrees of freedom (KK tower, string tower) appear → **duality at infinity** (T-duality, S-duality)

**Examples**:
- Extra-dim radius $R \to \infty$: KK tower $m_n \sim n/R \to 0$
- String coupling $g_s \to 0$: 1/g_s tower appears (S-duality)

### ③ Weak Gravity Conjecture (WGC, AMNV 2007)
**Principle**: **a gauge force stronger than gravity must exist**. An extremal BH must be able to decay into super-extremal particles.

**Formalization** (electric WGC):
$$\frac{|q|}{m} \geq \frac{Q_{\rm ext}}{M_{\rm ext}}\bigg|_{\rm BH}$$
- $q$: gauge charge of the particle, $m$: mass
- $Q/M$ for the extremal Reissner-Nordström BH

**Meaning**: a BH is *not stable as an extremal* — an emittable particle exists. The IR-UV bridge of quantum gravity (BH stability ↔ particle spectrum).

**Variants**:
- Magnetic WGC: magnetically charged particles
- Scalar WGC: scalar-field force
- Sublattice WGC (Heidenreich-Reece-Rudelius 2016): a sublattice of the charge lattice satisfies WGC
- Lattice WGC: every lattice point satisfies WGC
- BH WGC: applies to the BH state space itself

### ④ dS conjecture (OOSV 2018)
**Principle**: **the possibility of denying the consistency of the dS vacuum**. A stable dS vacuum does not arise in string theory.

**Formalization (raw)**:
$$\frac{|\nabla V|}{V} \geq c \sim O(1)\,\,(M_{\rm pl} = 1)$$
- $V$: scalar potential
- violates a dS vacuum ($V > 0, \nabla V = 0$)

**Refined (Andriot 2018, OOSV revised)**:
$$\frac{|\nabla V|}{V} \geq c \quad \text{or} \quad \frac{\min(\nabla^2 V)}{V} \leq -c'$$
- also allows saddle points

**Meaning**: a crisis of string-theory compatibility for our universe (accelerating expansion, $\Lambda > 0$, dS-like). **A direct challenge to the verifiability of inflation models / dark energy**.

**Direct connection with dS BH**: a deep motivation shared with 23's Susskind dS censorship / CLPW 2022 dS algebra formalism.

### ⑤ TCC (Trans-Planckian Censorship Conjecture, Bedroya-Vafa 2019)
**Principle**: **quantum fluctuations at the Planck scale must not cross the horizon**.

**Formalization**:
$$\Delta N < \ln\left(\frac{M_{\rm pl}}{H}\right)$$
- $\Delta N$: number of e-folds (inflation duration)
- $H$: Hubble scale

**Meaning**:
- the inflation-lifetime bound $\Delta N \sim 60$ (agreeing with observation) is derived naturally
- dS lifetime $\tau < (1/H)\ln(M_{\rm pl}/H)$ — the accelerating expansion of our universe is transient
- **verifiability crisis**: a limit on measurable dS dynamics
- parallels the dS BH Susskind dS censorship

### ⑥ Cobordism conjecture (McNamara-Vafa 2019)
**Principle**: **every quantum-gravity configuration is cobordism-equivalent to every other configuration**. The quantum-gravity cobordism group is trivial:
$$\Omega^{\rm QG}_d = 0 \quad \forall d$$

**Meaning**:
- topological generalization of ① No global symmetries
- topological classification of spacetime configurations — every configuration is *connected*
- deep universality of string theory (every vacuum is cobordism-connected)
- topological origin of charge conservation

**String Theory M-theory integration**: the topological origin of the fact that the 5 superstrings are unified into 11D M-theory.

## Mathematical structure

**Landscape vs Swampland definition**:
$$\text{Landscape} = \{\text{EFT} : \exists \text{ string completion}\}$$
$$\text{Swampland} = \{\text{EFT} : \text{looks consistent but } \nexists \text{ string completion}\}$$
$$\text{All consistent EFT} = \text{Landscape} \cup \text{Swampland}$$

**No global symmetries quantification**:
- BH no-hair: $\int_{\rm BH} *J = 0$ for global current $J$ (after sufficient time)
- Harlow-Ooguri 2018: *topological* action of the CFT extended operator → bulk gauge symmetry

**Distance conjecture formula**:
- moduli space $\mathcal M$ = scalar field configuration space
- Metric $g_{ij}(\phi) d\phi^i d\phi^j$
- Geodesic distance $d(\phi_0, \phi) = \int \sqrt{g_{ij}\dot\phi^i\dot\phi^j}\,dt$
- **Tower mass**: $m_n(\phi) = m_0 \cdot n^{1/k} \cdot e^{-\alpha d/M_{\rm pl}}$
  - $n$: tower index
  - $k$: tower power (KK: k=1, string: k=2)

**WGC formalization (electric)**:
- Reissner-Nordström extremal: $M_{\rm ext} = |Q|/\sqrt{G}$ (G = $1/M_{\rm pl}^2$)
- $\frac{Q_{\rm ext}}{M_{\rm ext}} = \sqrt{G} = 1/M_{\rm pl}$
- WGC: $\exists$ particle with $\frac{|q|}{m} \geq 1/M_{\rm pl}$
- i.e. $|q| M_{\rm pl} \geq m$ → "gauge force ≥ gravity force"

**Sharpened WGC** (Heidenreich-Reece-Rudelius 2016):
- **Sublattice WGC**: a WGC particle exists on the charge sublattice $\Gamma' \subset \Gamma$
- **Lattice WGC** (strong): a WGC particle at every charge lattice point

**dS conjecture (raw + refined)**:
- raw: $|\nabla V| \geq c V$
- refined (OOSV revised): $|\nabla V| \geq c V$ OR $\min(\nabla^2 V) \leq -c' V$
- denies a dS vacuum ($V > 0, \nabla V = 0, \nabla^2 V > 0$ all)

**TCC quantification**:
- e-fold $N = \int H\,dt$
- Trans-Planckian condition: quantum-fluctuation wavelength $\lambda_{\rm phys}(t) = a(t)/k$ vs Planck length $\ell_{\rm pl}$
- TCC: $\lambda_{\rm phys}(t_{\rm exit}) > \ell_{\rm pl}$ → $\Delta N < \ln(M_{\rm pl}/H)$
- dS lifetime: $H \cdot t_{\rm dS} < \ln(M_{\rm pl}/H)$

**Cobordism conjecture**:
- $\Omega_d^G$: the $d$-dimensional cobordism group of $G$-structure
- QG cobordism: $\Omega_d^{\rm QG} = 0$
- application: every charge defect has a trivial cobordism class → can be *resolved* by a bordism

**Distance + WGC integration** (Heidenreich-Reece-Rudelius 2018~):
- Tower mass $m_n \sim e^{-\alpha\phi}$ satisfies WGC → distance / WGC express the same mechanism
- "Distance WGC": a WGC particle tower appears at every infinite-distance limit point

**Operator algebra generalization attempt**:
- swampland conjectures in the algebra formalism — possible agreement with the degrees-of-freedom classification of the type III$_1$ algebra
- algebra-level integration of factorization / landscape (2024~)

## Why it matters

**Direct response to the String Theory landscape verifiability crisis**:
- $10^{500}$ landscape alone gives no prediction → swampland classification *derives constraints*
- shift from "every EFT is possible" to "specific EFTs are impossible"
- possibility of formalizing the general principle of quantum gravity

**Crisis of string-theory compatibility for our universe (accelerating expansion)**:
- the dS conjecture may deny the entry of our universe into the landscape
- crisis of string-theory models of inflation / dark energy
- motivation shared with dS BH Susskind dS censorship — the epistemological limit of quantum gravity of our universe

**Quantitative derivation of the general principles of quantum gravity**:
- WGC: BH stability ↔ particle spectrum (IR-UV bridge)
- Distance: moduli limit ↔ new degrees of freedom (duality)
- No global symmetries: directly connected to the BH information paradox
- Cobordism: topological classification → every vacuum connected

**Quantum-gravity upper bound of the Quantum Field Theory EFT framework**:
- the Wilson EFT assumes IR-UV separation — the swampland adds a quantum-gravity constraint to this assumption
- "which EFTs can be embedded in quantum gravity" → the boundary of the EFT framework

**Combination with the Factorization paradox ensemble interpretation**:
- ensemble vs single CFT of the landscape — the swampland as a tool for classifying ensemble members
- possible integration with the algebra-level answer of factorization

**Combination with the Operator algebra formalism (2024~)**:
- the motivation of the type III algebra degrees-of-freedom classification and the swampland conjectures
- modular flow + crossed product as the algebra dimension of the swampland

**Epistemological implication of the TCC**:
- limit on measurable dS dynamics → the *in-principle* limit of quantum gravity of our universe
- a string-theory constraint on the verifiability of inflation (CMB anisotropy / gravitational waves)

**Unsolved (self-identified)**:
- **Truth of the KKLT dS vacuum**: Sethi (KKLT critique) vs Kallosh (rebuttal) — existence of a string-theory dS
- **Exact form of the dS conjecture**: raw vs refined, exact values of $c, c'$
- **Sharpened form of WGC**: sublattice vs lattice, exact integration of electric vs magnetic
- **Logical relations among the conjectures**: which is more fundamental? (Distance ⇒ WGC? Cobordism ⇒ No global?)
- **Is our universe swampland or landscape**: the exact string-theory embedding of accelerating expansion
- **TCC and inflation models**: the natural 60 e-fold limit vs the crisis of eternal inflation
- **Completeness of the swampland program**: possibility of additional conjectures beyond the six (e.g. Festina Lente, Emergent String)

## Historical context

**Act I — Discovery of the landscape and the verifiability crisis (1995~2005)**:
- 1995 Bousso-Polchinski — flux compactification landscape ($\sim 10^{200}$)
- 1995~ Strominger-Vafa / Polchinski D-brane → unification of the 5 superstrings (M-theory)
- 2003 **KKLT** (Kachru-Kallosh-Linde-Trivedi, hep-th/0301240) — dS uplift candidate via anti-D3 brane (start of the controversy)
- 2003 **Susskind** — *The Anthropic Landscape of String Theory* — anthropic interpretation of the landscape + multiverse
- 2004 Douglas — flux vacua $\sim 10^{500}$
- 2005 **Vafa** — *The String Landscape and the Swampland* (hep-th/0509212) — **first formalization of the swampland concept**
- **Decisive turn**: 2005 Vafa — a *classification response* to the landscape verifiability crisis. Formalization of the statement that "not every EFT enters string theory"

**Act II — Formalization of the core conjectures (2006~2017)**:
- **2006 Ooguri-Vafa** — *On the Geometry of the String Landscape and the Swampland* (hep-th/0605264) — **Distance conjecture**
- **2007 Arkani-Hamed-Motl-Nicolis-Vafa** — *The String Landscape, Black Holes and Gravity as the Weakest Force* (hep-th/0601001) — **Weak Gravity Conjecture (WGC)**
- 2010 **Banks-Seiberg** — *Symmetries and Strings in Field Theory and Gravity* — strengthening of No global symmetries
- 2014~ Heidenreich-Reece-Rudelius — sublattice WGC, lattice WGC
- 2016 Heidenreich-Reece-Rudelius — *Sharpening the Weak Gravity Conjecture with Dimensional Reduction*
- 2017~ formalization of a catalog of swampland conjectures (Klaewer, Lust, etc.)
- **Decisive turn**: 2007 WGC — swampland conjectures became *quantitatively verifiable* (BH / black brane stability). Simple principle → measurable constraint

**Act III — dS crisis and TCC (2018~2020)**:
- **2018 OOSV (Obied-Ooguri-Spodyneiko-Vafa)** — *De Sitter space and the swampland* (arXiv:1806.08362) — **dS conjecture (raw)**
- 2018 Andriot — refined dS conjecture
- 2018 OOSV revised — allows saddle points
- 2019 KKLT critique / counter-critique (Sethi 2019 *Supersymmetry breaking by fluxes*; Kallosh rebuttal)
- **2019 Bedroya-Vafa** — *Trans-Planckian Censorship and the Swampland* (arXiv:1909.11063) — **TCC**
- 2019~ Palti — *The Swampland: Introduction and Review* (arXiv:1903.06239) — synthesis
- 2020 Bedroya-Brandenberger-Loverde-Vafa — TCC + inflation-model constraints
- 2020 Heidenreich-Reece-Rudelius — Emergent String conjecture (WGC + Distance integration)
- **Decisive turn**: 2018 OOSV — **the possibility that our universe (accelerating expansion) does not enter the landscape is raised in earnest**. An *epistemological crisis* of quantum gravity — is the universe we can measure incompatible with string theory?

**Act IV — Cobordism + integration + algebra combination (2019~)**:
- **2019 McNamara-Vafa** — *Cobordism Conjecture, Anomalies, and the Swampland* (arXiv:1909.10355) — **Cobordism conjecture**
- 2020~ Heidenreich-Reece-Rudelius — Sharpened distance, BH WGC, swampland of effective field theories
- 2020 Etheredge-Heidenreich-Rudelius — analysis of the logical relations among the conjectures
- 2022 Bedroya — Festina Lente conjecture (charged BH stability)
- 2022~ — combination of the Operator algebra CLPW 2022 dS algebra with the swampland dS conjecture
- 2023 Heidenreich-Reece-Rudelius — *The First Law of Gravitational Effective Field Theory* — generalizing the swampland into an EFT principle
- 2024~ — the swampland program is being integrated into a *general principle of quantum gravity* (4 cores + appendices)
- 2024 Vafa / Reece — comprehensive review of the swampland program (a candidate general principle of quantum gravity)
- **Decisive turn**: 2019 Cobordism — the swampland program was elevated from a mere collection of conjectures to a *topological classification dimension*. The mathematical formalization of the general principle of quantum gravity begins

**Key insight**: The Swampland program is a *direct response* to the String Theory landscape verifiability crisis. To the crisis of no verification of "all $10^{500}$ are possible," **Vafa 2005** presented a classification tool, and the **six conjectures** (No global symmetries / Distance / WGC / dS / TCC / Cobordism) formalize the *constraints* of the general principles of quantum gravity. The single most powerful result is the **2018 OOSV dS conjecture** — it raises the possibility that our universe (accelerating expansion, $\Lambda > 0$) does not enter the string-theory landscape, capturing at a different level the same *crisis of quantum gravity of our universe* as the dS BH Susskind dS censorship. As the **seventh note of the full quantum-gravity domain**, the Swampland formalizes the *constraint principles* of quantum gravity following the 22 algebra formal tools / 23 application to our universe / 21 ensemble interpretation — a tool for classifying "which EFTs can be completed into quantum gravity." But the truth of KKLT / the exact form of the dS conjecture / sharpened WGC / the logical relations among conjectures / whether our universe is swampland / the compatibility of TCC and inflation / the completeness of the program remain unsolved. With the appearance of the **2019 Cobordism conjecture**, the swampland was elevated to a topological classification dimension, and the attempt to combine it with the Operator algebra type III has been active since 2022~ — **a living discipline in which the mathematical formalization of the general principle of quantum gravity is underway**.

## Connections

**Prior connections** (already studied):
- String Theory — **direct bridge** (the response to the landscape verifiability crisis is the Swampland)
- dS BH — dS conjecture, deep motivation of Susskind dS censorship
- Factorization paradox — ensemble interpretation, single CFT vs ensemble of the landscape
- Operator algebra — integration of the algebra formalism with the swampland (2024~)
- Cosmology — accelerating expansion, inflation, cosmological constant — direct application of the dS conjecture
- Quantum Field Theory — quantum-gravity upper bound of the EFT framework, Wilson RG and the swampland
- General Relativity — Reissner-Nordström BH (WGC), dS metric
- Page curve — connection of No global symmetries with the BH information paradox
- Hawking radiation — BH no-hair, a direct instance of no-global-symmetries

**Next connections** (P2 new follow-ups):
- **Soft hair (HPS 2016) depth** — BMS algebra, BH surface degrees of freedom, the topological dimension of the swampland
- **Bulk reconstruction algebra** — integration of the 22 / 23 / 24 formalism
- **Static patch holography** — Susskind / Banks follow-up, dS swampland connection
- **Celestial CFT depth** — flat holography boundary state, the flat dimension of the swampland
- **LQG / CDT alternative quantum gravity** (P3) — possibility of generalizing the swampland in non-string quantum gravity

**Related concepts / deep dives**:
- KKLT vs LVS (Large Volume Scenario) — dS uplift attempts
- Festina Lente (Bedroya 2022) — charged BH WGC strengthening
- Emergent String conjecture — WGC + Distance integration
- Anti-de Sitter Distance conjecture (Lust-Palti-Vafa)
- Quintessence vs Cosmological constant — the dS conjecture correspondence
- Tadpole conjecture (Bena-Blumenhagen-Grana-Plauschinn-Shukla)
- Festina Lente bound on charged particle mass

**arXiv / literature**:
- **Vafa 2005** — *The String Landscape and the Swampland*, arXiv:hep-th/0509212
- **Ooguri-Vafa 2006** — *On the Geometry of the String Landscape and the Swampland*, arXiv:hep-th/0605264
- **Arkani-Hamed-Motl-Nicolis-Vafa 2007** — *The String Landscape, Black Holes and Gravity as the Weakest Force*, arXiv:hep-th/0601001
- Banks-Seiberg 2010 — *Symmetries and Strings in Field Theory and Gravity*, arXiv:1011.5120
- Heidenreich-Reece-Rudelius 2016 — *Sharpening the Weak Gravity Conjecture*, arXiv:1606.08437
- Harlow-Ooguri 2018 — *Symmetries in quantum field theory and quantum gravity*, arXiv:1810.05338
- **OOSV 2018** — *De Sitter space and the swampland*, arXiv:1806.08362
- **Bedroya-Vafa 2019** — *Trans-Planckian Censorship and the Swampland*, arXiv:1909.11063
- **McNamara-Vafa 2019** — *Cobordism Conjecture, Anomalies, and the Swampland*, arXiv:1909.10355
- **Palti 2019** — *The Swampland: Introduction and Review*, arXiv:1903.06239 — comprehensive review
- van Beest-Calderon-Mishra-Valandro-Westphal 2021 — *Lectures on the Swampland Program in String Compactifications*, arXiv:2102.01111
- Vafa / Reece 2024 — comprehensive review of the swampland program as a general principle of quantum gravity
- textbooks: Polchinski *String Theory* (vols 1, 2); Becker-Becker-Schwarz *String Theory and M-Theory*; van Beest et al. lectures

## Workflow Connections
- index
- physicist
- modern-README
- String Theory
- dS BH
- Operator algebra
- Factorization paradox
- Cosmology
- Quantum Field Theory
