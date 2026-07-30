# dS BH

## One-line essence
AdS/CFT is confined to negative-curvature (AdS) environments — our universe is **asymptotically flat** (Schwarzschild, Kerr) or **asymptotically de Sitter** ($\Lambda>0$, accelerating expansion), and AdS/CFT cannot be applied because the boundary CFT is absent; but **in the flat case BMS supertranslation + Soft hair (HPS 2016) + celestial sphere holography**, and **in the dS case the Operator algebra CLPW 2022 type II$_1$ observer algebra + crossed product**, provide the natural generalization of information encoding and generalized entropy, while **Susskind static patch holography + Bousso covariant entropy bound** are candidate formalizations of dS holography — **the sixth note of the full quantum-gravity domain, the natural next step in which the 22 algebra formalism applies directly to the BHs of our universe**.

## Physical picture

**Why AdS/CFT is insufficient for our universe**:
- AdS = reflective box. Information returns forever at the boundary, information conservation is trivial — a toy model
- Our universe: ① around stars/BHs it is asymptotically flat (Minkowski at Schwarzschild $r\to\infty$) ② the whole universe is dS (1998 SNe Ia discovery of accelerating expansion, $\Lambda \approx 10^{-122} M_{\rm pl}^4$)
- The timelike boundary of AdS is absent in flat/dS → a one-to-one boundary CFT cannot be defined
- The Cosmology results (accelerating expansion + cosmological constant) designate dS as the true stage of quantum gravity

**Analogy — three universes**:
- AdS universe: mirror room (information conserved forever, AdS/CFT)
- flat universe (Schwarzschild): open infinity — information can escape to future infinity $\mathcal I^+$
- dS universe (cosmological horizon): finite causal region (static patch), **a different horizon for each observer**

**Flat BH (Schwarzschild, Kerr) information paradox**:
- Hawking 1976: information loss in an asymptotically flat environment — **this is the original information paradox**
- What AdS/CFT solves is the AdS cousin, not the original problem
- In the flat case the boundary is null infinity ($\mathcal I^\pm$, scri) — the timelike boundary is absent
- The S-matrix is definable (in-state $\mathcal I^-$ → out-state $\mathcal I^+$) → **S-matrix program**

**BMS group and Soft hair** (Strominger, HPS 2016):
- The asymptotic symmetry group of $\mathcal I^\pm$ of flat spacetime = BMS (Bondi-Metzner-Sachs)
- Lorentz × supertranslation (infinite-dimensional!)
- supertranslation: $u \to u + f(\theta,\phi)$ — deformation of the null cut
- Strominger soft theorems = BMS Ward identity (sharpening of the Weinberg 1965 soft graviton)
- **HPS 2016 (Hawking-Perry-Strominger)**: infinitely many soft photon/graviton hair on the BH surface → the possibility that **information is encoded in the BMS degrees of freedom on the BH surface**
- Hawking's last paper — "BHs are not bald"

**Celestial sphere holography** (Pasterski-Shao 2017, Strominger):
- 4D flat spacetime $\leftrightarrow$ CFT on the 2D celestial sphere
- the null generators of $\mathcal I^\pm$ correspond to points on the celestial sphere
- BMS = conformal symmetry (extension) of the celestial CFT
- a flat holography candidate, but a precise definition of the boundary state is incomplete

**dS BH and the cosmological horizon**:
- de Sitter: $ds^2 = -(1-H^2 r^2)dt^2 + (1-H^2 r^2)^{-1}dr^2 + r^2 d\Omega^2$, $H^2 = \Lambda/3$
- $r = H^{-1}$ = cosmological horizon (the distance light cannot reach)
- **a different static patch for each observer worldline** (causal diamond)
- Gibbons-Hawking 1977: a dS observer perceives thermal $T_{\rm dS} = H/2\pi$ + horizon entropy $S_{\rm dS} = A/4G = 3\pi/(\Lambda G)$
- Schwarzschild-de Sitter: two horizons (BH + cosmological), each with a Hawking temperature

**dS observer algebra** (CLPW 2022, direct generalization of Operator algebra):
- observable algebra of the static patch
- Large $N$ → type III$_1$ (gravity)
- Crossed product with the observer worldline → **type II$_1$** (finite trace, finite total entropy!)
- Generalized entropy in dS:
$$S_{\rm gen}^{\rm dS} = \frac{A_{\rm horizon}}{4G_N} + S_{\rm matter}^{\rm static\ patch} = -\tau(\rho\ln\rho)$$
- **finite trace** = algebra-formalism formalization of the dS finite-degrees-of-freedom hypothesis (Banks-Fischler 1999)

**Susskind static patch holography** (2003~):
- the static patch of a single observer is the fundamental unit of dS holography
- a different observer is a different patch — **observer-dependent holography**
- AdS "global" holography ↔ dS "patch" holography
- Bousso N-bound: dS entropy finite → Hilbert-space dimension $\dim\mathcal H \sim e^{S_{\rm dS}}$

**Bousso covariant entropy bound** (1999):
- entropy on an arbitrary light-sheet $L$ is $\leq A(\partial L)/4G$
- covariant generalization of the holographic principle (1993 't Hooft, 1995 Susskind)
- applies to AdS, flat, and dS alike — **a universal principle of holography**
- covariant form of the generalized second law

**dS Page curve attempt**:
- information recovery of a BH in dS (Schwarzschild-de Sitter)
- generalization of the island formula for two horizons
- 2022~ Aalsma, Lopes, etc.: attempt to formalize the dS Page curve, incomplete

**Susskind dS censorship conjecture** (2021~):
- dS quantum gravity may be ill-defined — only meta-stable landscape vacua are meaningful
- true dS dynamics cannot be measured at long times (are slow-roll inflation / dark energy all transient?)
- parallels the String Theory Swampland dS conjecture (Vafa, Obied-Ooguri-Spodyneiko-Vafa 2018: $|\nabla V|/V \geq c$)

**Flat generalization of the Factorization paradox**:
- Multi-boundary in flat space — the flat cousin of the Maldacena-Maoz wormhole
- algebra-level answer to the wormhole in a single asymptotic region (natural in type III)

## Mathematical structure

**Schwarzschild metric** (asymptotically flat):
$$ds^2 = -\left(1 - \frac{2GM}{r}\right)dt^2 + \left(1 - \frac{2GM}{r}\right)^{-1}dr^2 + r^2 d\Omega^2$$
- Event horizon: $r_H = 2GM$
- Hawking temperature: $T_H = \hbar c^3/(8\pi G M k_B)$
- Bekenstein-Hawking: $S_{BH} = A/(4G\hbar) = 4\pi G M^2/\hbar$

**Kerr metric** (rotating BH):
$$ds^2 = -\left(1 - \frac{2GMr}{\Sigma}\right)dt^2 + \cdots,\quad \Sigma = r^2 + a^2\cos^2\theta$$
- two horizons $r_\pm = GM \pm \sqrt{G^2M^2 - a^2}$
- Ergosphere — Penrose process

**de Sitter metric** (static patch):
$$ds^2 = -(1 - H^2 r^2)dt^2 + (1 - H^2 r^2)^{-1}dr^2 + r^2 d\Omega^2,\quad H^2 = \Lambda/3$$
- Cosmological horizon: $r_c = H^{-1}$
- $T_{\rm dS} = H/(2\pi) = \hbar/(2\pi)\sqrt{\Lambda/3}$
- $S_{\rm dS} = \pi/(GH^2) = 3\pi/(\Lambda G)$ — **finite**

**Schwarzschild-de Sitter** (BH in dS):
$$f(r) = 1 - \frac{2GM}{r} - \frac{H^2 r^2}{3}$$
- two horizons (BH + cosmological), merging at the Nariai limit $r_{\rm BH} = r_c$
- the two horizons cannot be in thermal equilibrium (different temperatures) → non-equilibrium

**BMS group** (4D flat asymptotics):
$$\text{BMS}_4 = \text{SO}(3,1) \ltimes \text{Supertranslations}$$
- Supertranslation: $u \to u + T(z, \bar z)$, $T \in C^\infty(S^2)$ (infinite-dimensional)
- Lorentz rotates the supertranslations — semi-direct product
- Extension: super-rotation (Diff$(S^2)$) — the conformal group of the celestial CFT

**Strominger soft theorems**:
- Leading soft graviton: BMS supertranslation Ward identity
$$\lim_{\omega\to 0}\omega \mathcal M_{n+1}(\text{soft}) = (\text{soft factor}) \times \mathcal M_n$$
- Subleading: super-rotation Ward identity
- Soft photon: large gauge transformation

**Soft hair on BH** (HPS 2016):
- supertranslation charge $T_f$ on the BH surface:
$$T_f = \frac{1}{16\pi G}\oint_{\rm horizon} f \, \omega$$
- infinitely many conserved charges → **soft hair**
- a candidate for information encoded as hair, but whether the hair holds enough information is unsolved

**Celestial CFT** (Pasterski-Shao):
- 4D scattering amplitude → 2D celestial sphere amplitude (Mellin transform)
- $\mathcal A_n(\Delta_i, z_i, \bar z_i)$ — transformed into primary operators
- BMS → celestial conformal symmetry

**dS observer algebra** (CLPW 2022, **direct generalization of 22**):
- observables $\mathcal A_{\mathcal P}$ of the static patch $\mathcal P$
- Bulk fields + observer's clock $H_{\rm obs}$
- Crossed product: $\mathcal A_{\mathcal P} \rtimes_\sigma \mathbb R$
  - $\sigma_t$ = boost along observer worldline (modular flow)
- **Type II$_1$** — finite trace $\tau$ ($\tau(1) < \infty$)
- Generalized entropy:
$$S_{\rm gen}^{\rm dS}(\rho) = -\tau(\rho\ln\rho) = \frac{A_{\rm horizon}}{4G_N} + S_{\rm matter}^{\mathcal P}$$
- **finite-dimensional Hilbert-space candidate** ($\dim \sim e^{S_{\rm dS}}$, Banks-Fischler 1999)

**Bousso covariant bound**:
$$S(L) \leq \frac{A(\partial L)}{4G\hbar}$$
- $L$ = light-sheet (null geodesic congruence), $\partial L$ = boundary 2-surface
- applies to AdS, flat, dS, and general spacetimes
- strengthens the generalized second law

**dS Page curve candidate** (Schwarzschild-de Sitter, 2022~):
- BH evaporation in a dS background
- Two-horizon entropy: $S_{\rm BH}(t) + S_{\rm cosmo}$
- generalization of the island formula:
$$S(R) = \min\,\text{ext}\left[\frac{A(\partial I)/4G + S_{\rm bulk}(R\cup I)}{...}\right]$$
- Two extrema (BH horizon island + cosmological island) compete
- the quantitative curve is incomplete

**Strominger dS/CFT** (2001):
- $Z_{\rm dS} \stackrel{?}{=} \langle \mathcal O_1\cdots\mathcal O_n\rangle_{\rm CFT_{Eucl}}$
- spacelike CFT of $\mathcal I^\pm$ (non-unitary?)
- incomplete — boundary absent, observer-independence absent

**Susskind dS censorship**:
- $\rm Long-time \ correlator \to thermal$ — the observer cannot measure it
- true dS dynamics is mathematically definable but there is a physical measurement limit
- Inflation / dark energy are transient → true eternal dS is metastable

## Why it matters

**Direct application to our universe**:
- whereas AdS/CFT is a toy, flat/dS are real
- Schwarzschild, Kerr = the asymptotic environment of every star / BH in the universe
- dS = the quantum-gravity stage of the universe itself (accelerating expansion, $\Lambda$, inflation)
- the Cosmology discovery of accelerating expansion (1998) made dS quantum gravity urgent

**Direct generalization of Operator algebra**:
- CLPW 2022 dS observer algebra = type II$_1$ (finite trace)
- AdS type III$_1$ (gravity) → dS II$_1$ — finite-Hilbert-space hypothesis
- the crossed-product formalism applies naturally to dS / flat
- the dS definition of generalized entropy — the 22 formalism carried over intact to our universe

**The original environment of the information paradox**:
- raised by Hawking 1976 in the asymptotically flat case — AdS is a detour
- BMS, soft hair, celestial holography are candidate flat answers
- the possibility that information is encoded in the BMS degrees of freedom on the BH surface + null infinity

**Combination of the String Theory landscape with the dS crisis**:
- Swampland dS conjecture ($|\nabla V|/V \geq c$) — difficulty of a string-theory dS vacuum
- Susskind censorship — dS quantum gravity may be ill-defined
- the ensemble interpretation of the landscape is natural as the metastable vacua of dS

**Flat generalization of the Factorization paradox**:
- Multi-asymptotic-region wormhole in flat space
- absence of a single CFT — absence of a boundary itself
- the type III formalism is more natural in the flat case

**Bousso covariant bound = universal principle of holography**:
- generalizes the AdS holography conjecture to all spacetimes
- covariant form of the generalized second law
- the most universal entropy bound of quantum gravity

**Unsolved (self-identified)**:
- **dS Page curve quantification**: the exact curve of the two-horizon island competition is incomplete
- **The boundary state of flat holography**: is the celestial CFT the exact dual?
- **dS Hilbert-space dimension**: is finite $e^{S_{\rm dS}}$ the exact formalization?
- **The truth of Susskind dS censorship**: is dS definable?
- **Whether soft hair holds enough information**: follow-up verification of HPS 2016
- **Our-universe verification of the Operator algebra type II$_1$**: algebra application to inflation / dark energy

## Historical context

**Act I — Formalization of the flat BH information paradox (1916~1976)**:
- 1916 Schwarzschild — asymptotically flat BH (spacetime outside a star)
- 1939 Oppenheimer-Snyder — gravitational collapse
- 1963 Kerr — rotating BH (model of a real astrophysical BH)
- 1965 Penrose — singularity theorem + null infinity $\mathcal I^\pm$
- 1962 Bondi-Metzner-Sachs — formalization of the flat asymptotic symmetry group (BMS)
- **1972 Bekenstein** — BH entropy $S \propto A$
- **1974 Hawking** — Hawking radiation of the Schwarzschild BH ($T_H = \hbar c^3/8\pi GM k_B$)
- 1975 Hawking — BH four laws
- **1976 Hawking** — *Breakdown of predictability in gravitational collapse*. Hypothesis of **information loss in an asymptotically flat environment** — **the original information paradox**
- **Decisive turn**: 1976 Hawking — information loss is an asymptotically flat spacetime hypothesis, AdS is a later detour. **The original problem is flat**

**Act II — Discovery of dS and holography attempts (1997~2010)**:
- 1977 Gibbons-Hawking — dS thermal $T = H/2\pi$, horizon entropy $S = A/4G$
- 1995 't Hooft / Susskind — Holographic principle (flat / general)
- **1997 Maldacena** — AdS/CFT (negative-curvature detour)
- **1998 Riess / Perlmutter (SNe Ia)** — discovery of cosmic accelerating expansion → our universe is dS
- 1999 Banks-Fischler — dS finite-Hilbert-space conjecture
- **1999 Bousso** — covariant entropy bound (holography for all spacetimes)
- **2001 Strominger** — *dS/CFT correspondence* (boundary-absence problem, incomplete)
- 2003 Susskind — *Anthropic landscape of string theory* — metastable dS of the landscape
- 2010 Maloney-Strominger — dS partition function, ensemble interpretation
- **Decisive turn**: 1998 discovery of cosmic accelerating expansion — AdS is a toy, **dS is real**. A change of the true stage of quantum gravity

**Act III — Soft hair, BMS, celestial holography (2014~2021)**:
- 2014~ Strominger — *Soft theorems and BMS symmetry* lecture series
- 2014 Strominger-Zhiboedov — soft graviton theorem = BMS Ward identity
- **2016 HPS (Hawking-Perry-Strominger)** — *Soft hair on black holes* — Hawking's last paper. Infinite supertranslation charge on the BH surface → information encoding
- 2017 Pasterski-Shao — *Conformal basis for flat space amplitudes* — celestial CFT
- 2017 Strominger — *Lectures on the infrared structure of gravity and gauge theory*
- 2019~ Almheiri / Engelhardt — Page curve in flat space attempt (island in non-AdS)
- 2020~ Aalsma / Lopes — dS Page curve attempt
- 2021 Susskind — strengthening of the dS censorship conjecture
- **Decisive turn**: 2016 HPS Soft hair — Hawking himself first proposed a candidate answer to the flat information paradox. New paradigm that BHs are "not bald"

**Act IV — dS / flat generalization of operator algebra (2022~)**:
- **2022 June CLPW** (Chandrasekaran-Longo-Penington-Witten, arXiv:2206.10780) — *An algebra of observables for de Sitter space*. **Type II$_1$ observer algebra, dS generalized entropy definition**
- 2022 September CPW — Large N algebras (AdS) — Operator algebra
- 2023 Witten — *Algebras, regions, and observers* — integrated dS / BH interior algebra
- 2023~ Penington / Witten et al. — flat space algebra attempt (type III + null infinity)
- 2024~ Susskind / Maloney / Bousso — progress in static patch holography
- 2024~ Strominger / Pasterski et al. — integration of celestial holography + BMS algebra
- 2025 — dS Page curve formalization underway, combined with the String Theory Swampland dS conjecture
- **Decisive turn**: 2022 CLPW — **the 22 algebra provided a formalism directly applicable to our universe (dS)**. The AdS type III$_1$ → dS type II$_1$ conversion precisely formalizes the finite-Hilbert-space hypothesis. **The sixth note of the full quantum-gravity domain is the natural generalization of 22**

**Key insight**: AdS/CFT provided a precise definition of quantum gravity but is a **toy** — our universe is asymptotically flat (around stars/BHs) or dS (the whole universe). **23 Flat / dS BH generalization** applies quantum gravity to our universe. In the flat case, the 1962 BMS group + 2014~ Strominger soft theorems + 2016 HPS Soft hair provide a candidate mechanism for encoding information on the BH surface, and in the dS case, 1977 Gibbons-Hawking + 1999 Bousso bound + 2022 CLPW type II$_1$ observer algebra provide a finite-Hilbert-space + generalized-entropy definition. The core of this note is that **the formalism of 22 Operator algebra applies directly to our universe (dS)** — the type III$_1$ (AdS gravity) → type II$_1$ (dS observer) conversion precisely formalizes the finite-degrees-of-freedom hypothesis of our universe at the algebra level. But the dS Page curve quantification, the exact dual of the celestial CFT, the truth of Susskind dS censorship, the information sufficiency of Soft hair, and the combination with the String Theory Swampland dS conjecture remain unsolved — **a living open problem of the full quantum-gravity domain (fifth depth note, verifying domain settling N=5)**.

## Connections

**Prior connections** (already studied):
- Operator algebra — **direct bridge** (CLPW 2022 dS algebra is the dS formal tool of this note)
- Factorization paradox — flat multi-asymptotic wormhole, absence of a single CFT
- String Theory — Swampland dS conjecture ($|\nabla V|/V \geq c$), metastable dS of the landscape
- Replica wormholes — wormhole in flat/dS attempt
- Page curve — flat / dS Page curve generalization (island formula)
- Hawking radiation — raised by Hawking 1976 in the asymptotically flat case (original information paradox)
- AdS/CFT — the object of comparison (negative-curvature toy vs real flat/dS)
- Quantum Information — the quantum-information dimension of the celestial CFT
- Cosmology — 1998 accelerating expansion, $\Lambda$, discovery of the dS universe
- General Relativity — direct use of the Schwarzschild / Kerr / dS metrics
- Special Relativity — flat spacetime asymptotics, $\mathcal I^\pm$
- Quantum Field Theory — soft theorem (Weinberg 1965), QFT in curved spacetime

**Next connections** (P2 new follow-ups):
- **Swampland conjecture in earnest** — Vafa 2005, dS conjecture, distance / weak gravity
- **Soft hair depth** — HPS 2016 follow-up, application of BMS algebra to the BH interior
- **Bulk reconstruction algebra dimension** — flat / dS application of the 22 formalism
- **Static patch holography** — Susskind / Banks follow-up
- **Celestial CFT depth** — Pasterski-Shao follow-up, flat holography
- **LQG / CDT alternatives** (P3) — non-string dS quantum-gravity candidates

**Related concepts / deep dives**:
- Penrose diagram of de Sitter (compact static patch)
- Nariai limit (Schwarzschild-dS extremal)
- Thermofield double in dS — two static patches
- ER=EPR in dS (CLPW 2023, observer entanglement)
- Stretched horizon (Susskind) dS application
- Asymptotic charges and large gauge transformations
- Memory effect (gravitational memory) — observable effect of BMS
- Gravitational wave memory (LIGO-verifiable?)

**arXiv / literature**:
- **Hawking 1976** — *Breakdown of predictability in gravitational collapse*, Phys. Rev. D 14
- **Gibbons-Hawking 1977** — *Cosmological event horizons, thermodynamics, and particle creation*, Phys. Rev. D 15
- **Bousso 1999** — *A covariant entropy conjecture*, JHEP 9907:004
- **Strominger 2001** — *The dS/CFT correspondence*, JHEP 0110:034
- Banks 2003 — Some thoughts on the quantum theory of de Sitter space
- **HPS 2016** — *Soft hair on black holes*, Phys. Rev. Lett. 116:231301
- Pasterski-Shao 2017 — *Conformal basis for flat space amplitudes*, Phys. Rev. D 96
- Strominger 2017 — *Lectures on the infrared structure of gravity and gauge theory* (Princeton)
- **CLPW 2022** — *An algebra of observables for de Sitter space*, arXiv:2206.10780
- Witten 2023 — *Algebras, regions, and observers*, arXiv:2303.02837
- Maloney-Strominger 2010 — Quantum gravity partition functions in three dimensions
- Aalsma-Lopes 2022~ — dS Page curve series
- Obied-Ooguri-Spodyneiko-Vafa 2018 — *De Sitter space and the swampland*, arXiv:1806.08362
- Susskind 2021~ — dS censorship series
- textbooks: Spradlin-Strominger-Volovich *Les Houches lectures on de Sitter space*; Strominger *Lectures on the infrared structure*

## Workflow Connections
- index
- physicist
- modern-README
- Operator algebra
- Factorization paradox
- String Theory
- Page curve
- Hawking radiation
- AdS/CFT
- Cosmology
- General Relativity
