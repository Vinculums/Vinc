# Celestial CFT

## One-line essence
The program of rewriting the scattering amplitudes of 4D flat spacetime as a CFT on the 2D **celestial sphere** — the **Pasterski-Shao 2017** Mellin transform is the foundation, and the Soft Hair on Black Holes **IR Triangle** (soft theorem ↔ BMS Ward ↔ memory) is sharpened into *celestial conformal symmetry*. The exact dual structure of a **candidate 2D non-unitary CFT** + Carrollian holography (Bagchi / Donnay-Marteau 2021~) + the flat cousin of Static Patch Holography. Synthesized in 2024 by Pasterski / Strominger / Donnay — *the formal definition of flat holography is in progress*. **The second instance of an external extension of the full quantum-gravity domain** (28 dS + 29 flat, U-13 stage-5 N=3 reachable).

## Physical picture

**Why Celestial CFT is needed**:
- 4D flat spacetime = the asymptotic environment of our universe (around stars / BHs)
- AdS/CFT has a clear boundary CFT, but the flat case *lacks a boundary*
- the IR Triangle achieved a 50-year integration, but the *holographic dual* is incomplete
- **the celestial sphere = the collection of all null directions = 2D**
- 4D scattering amplitudes → converted into 2D celestial CFT amplitudes

**Analogy — a celestial sphere studded with stars**:
- at future infinity ($\mathcal I^+$) every null direction corresponds to a point of $S^2$ (the celestial sphere)
- scattered particles = *stars (operator insertions)* on the celestial sphere
- 4D Lorentz = 2D conformal — the groups are the same!
- the celestial sphere = the boundary candidate of flat holography

**Pasterski-Shao 2017 — Mellin transform**:
- 4D primary state $|\Delta, p\rangle$
- Mellin transform: $\int_0^\infty \omega^{\Delta-1} d\omega$
- 2D conformal primary $\mathcal O_\Delta(z, \bar z)$ on $S^2$
- $z, \bar z$ = celestial sphere coordinates (stereographic projection)
- **conformal basis** for 4D scattering amplitudes

**IR Triangle = celestial CFT** (Strominger series):
| 4D expression | 2D celestial expression |
|---|---|
| Soft graviton theorem | Stress tensor Ward identity |
| BMS supertranslation | Conformal symmetry (extended BMS) |
| Memory effect | Vacuum deformation (vertex operator) |
| Subleading soft graviton | Super-rotation (Diff $S^2$) |

**Conformal symmetry on the celestial sphere**:
- 4D Lorentz $SL(2, \mathbb C)$ ↔ 2D global conformal $SL(2, \mathbb C)$ — the same group!
- Lorentz boost = 2D dilatation
- Lorentz rotation = 2D rotation
- the Lorentz invariance of 4D scattering = the conformal symmetry of the 2D celestial CFT

**Extended BMS = strengthening of celestial symmetry**:
- BMS supertranslation ⊂ Diff($S^2$)
- Cachazo-Strominger super-rotation = part of the 2D Virasoro
- celestial symmetry = $\text{Diff}(S^2) \ltimes \text{Supertranslations}$
- = celestial Virasoro + supertranslations

**Mellin transform formalization**:
$$\mathcal O_\Delta(z, \bar z) = \int_0^\infty \omega^{\Delta-1} \mathcal O(\omega, \hat n)\,d\omega$$
- $\mathcal O(\omega, \hat n)$ = energy $\omega$, direction $\hat n$ on $S^2$
- $\Delta$ = celestial scaling dimension

**Sharpening of the Soft Hair on Black Holes IR Triangle**:
- Soft graviton theorem (Weinberg 1965) → celestial stress tensor OPE
- BMS supertranslation Ward → celestial CFT $T \cdot \mathcal O$ OPE
- Memory effect → vacuum sector deformation
- **the 3 axes are *the 3 fundamental structures of the celestial CFT***

**Conformally soft theorems** (Donnay-Pasterski-Puhm 2018~):
- $\Delta = 1$ (graviton soft limit)
- $\Delta = 0$ (subleading soft)
- $\Delta = -1, -2, \ldots$ (sub-subleading)
- the *infinite tower of conformal soft modes* of the celestial CFT

**Carrollian holography** (Bagchi 2010~, Donnay-Marteau 2021~):
- the intrinsic geometry of $\mathcal I^+$ = Carrollian (no time, space only)
- a candidate *true dual* of flat holography (on $\mathcal I^+$ itself, not on the celestial sphere)
- celestial vs Carrollian — two candidates for flat holography
- 2024~ Donnay / Bagchi series — integration attempt

**The flat cousin of Static Patch Holography**:
- dS static patch (28) ↔ flat asymptotic ($\mathcal I^\pm$) celestial
- observer-dependent (28) ↔ global asymptotic (29)
- the two formalisms are *the two faces of flat/dS* — the two aspects of quantum gravity of our universe

**The non-unitarity problem**:
- the celestial CFT is a candidate *non-unitary* 2D CFT
- 4D unitary scattering → 2D non-unitary CFT
- the exact dual structure is incomplete (the biggest open problem)

**Flat generalization of the Bulk reconstruction**:
- AdS reconstruction (HKLL, JLMS)
- flat reconstruction = expressed as celestial CFT primaries
- bulk operator = celestial primary $\mathcal O_\Delta(z)$
- in progress — the exact mapping incomplete

**Black hole + celestial CFT**:
- BH formation → conformal-field deformation of the celestial CFT
- Strominger-Zhiboedov memory = celestial vacuum deformation
- soft hair = the zero-mode hair of the celestial CFT
- a candidate answer to the BH information paradox (sharpening of 25 Soft hair)

## Mathematical structure

**Stereographic projection** (Minkowski $\mathcal I^+$ → $S^2 \times \mathbb R_u$):
- Null direction $\hat n^\mu = (1, \sin\theta\cos\phi, \sin\theta\sin\phi, \cos\theta)$
- $S^2$ coordinates: $z = \cot(\theta/2) e^{i\phi}$, $\bar z = \cot(\theta/2) e^{-i\phi}$
- $u = t - r$: retarded time

**Mellin transform** (4D primary → 2D primary):
$$\mathcal O_\Delta(z, \bar z) = \int_0^\infty d\omega\, \omega^{\Delta-1} \mathcal O(p^\mu)$$
- $p^\mu = \omega \hat n^\mu(z, \bar z)$
- $\Delta$ ∈ $\mathbb C$ (principal series for unitary)

**Celestial conformal Ward identity**:
$$\langle T(z) \mathcal O_{\Delta_1}(z_1) \cdots \rangle = \sum_i \left[\frac{h_i}{(z-z_i)^2} + \frac{\partial_i}{z-z_i}\right]\langle \cdots \rangle$$
- $T(z)$ = celestial stress tensor
- $h_i = (\Delta_i + s_i)/2$ — celestial conformal weight
- $s_i$ = spin of the bulk particle

**Soft graviton ↔ stress tensor**:
- Leading soft graviton = celestial stress tensor $T(z)$ insertion
- $T(z) = \int_0^\infty d\omega\, \omega \mathcal O_{\rm graviton}(\omega, z)$ ($\Delta = 2$ Mellin)
- BMS supertranslation Ward = $T(z)$ Ward identity

**Soft photon ↔ Kac-Moody current**:
- Leading soft photon = $J^a(z)$ celestial current
- Large gauge transformation = celestial current Ward
- Kac-Moody algebra in 2D CFT

**Conformal primary** $\mathcal O_{\Delta, h, \bar h}(z, \bar z)$:
- Scaling dimension $\Delta = h + \bar h$
- Spin $s = h - \bar h$
- 4D massless particle: $h = (\Delta + s)/2$, $\bar h = (\Delta - s)/2$

**Celestial OPE**:
$$\mathcal O_{\Delta_1}(z_1) \mathcal O_{\Delta_2}(z_2) \sim \sum_\Delta \frac{C_\Delta(z_{12}, \bar z_{12})}{|z_{12}|^{2(h_1+h_2-h)}} \mathcal O_\Delta(z_2)$$
- $z_{12} = z_1 - z_2$
- $C_\Delta$ = OPE coefficients

**BMS = Virasoro × Supertranslations** (extended):
- Virasoro $L_n$, $\bar L_n$ ($n \in \mathbb Z$): 2D conformal
- Supertranslation $T_{m,n}$: zero-mode primaries
- Central charge $c$? — unsolved (matching with 4D bulk gravity)

**Carrollian algebra** (alternative dual):
- $[L_n, P_m] = ((s-1)n - m) P_{n+m}$
- the intrinsic symmetry of $\mathcal I^+$
- a *time-like* dimension different from the celestial one

**Conformally soft theorems** (Donnay-Pasterski-Puhm):
- $\Delta = 1$: leading soft graviton (BMS supertranslation)
- $\Delta = 0$: subleading (super-rotation)
- $\Delta = -1, -2, \ldots$: the infinite tower of sub-subleading
- each $\Delta$ is a *new conformal symmetry*

**4D scattering amplitudes → 2D celestial amplitudes**:
$$\mathcal A_n(p_1, \ldots, p_n) \to \tilde{\mathcal A}_n(\Delta_1, z_1, \ldots, \Delta_n, z_n)$$
- $\tilde{\mathcal A}_n = \prod_i \int d\omega_i \omega_i^{\Delta_i - 1} \mathcal A_n$
- celestial amplitude = 2D conformal correlator

**Celestial CFT amplitude formalization**:
- $\langle \mathcal O_{\Delta_1}(z_1) \cdots \mathcal O_{\Delta_n}(z_n)\rangle_{\rm CCFT} = \tilde{\mathcal A}_n$
- Celestial primary 4-point function = the Mellin transform of MHV / N$^k$MHV amplitudes

**Celestial sharpening of the Soft Hair on Black Holes HPS 2016**:
- BMS supertranslation = celestial $T(z)$ Ward
- BH-surface supertranslation hair = celestial vacuum sector
- soft hair = the *zero-mode* of a celestial CFT primary

## Why it matters

**A candidate formal definition of flat holography**:
- AdS/CFT 1997 → the formal appearance of flat holography 30 years later
- 1998 discovery of accelerating expansion + 2014 IR Triangle + 2017 celestial CFT
- the second formalization of *quantum gravity of our universe* (together with 28 dS)

**Sharpening of the Soft Hair on Black Holes IR Triangle**:
- formalizes that soft theorem, BMS, and memory are *the 3 fundamental structures of the celestial CFT*
- HPS 2016 Soft hair = celestial vacuum deformation
- all the results of 25 restated at the celestial level

**Flat generalization of the Bulk reconstruction**:
- AdS reconstruction → flat reconstruction (celestial primary)
- bulk operator = celestial CFT primary
- the flat cousin of HKLL / JLMS

**The flat dimension of the Swampland**:
- No global symmetries → absence of global symmetries of the celestial CFT
- WGC → the charged-operator spectrum of the celestial CFT
- the flat-holography dimension of the swampland conjectures

**The flat cousin of Static Patch Holography**:
- dS static patch (observer-dependent) ↔ flat celestial (global asymptotic)
- the two formalisms are *the two faces of quantum gravity of our universe*
- 28 + 29 = the two axes of the sharpening of quantum gravity of our universe

**Carrollian holography integration**:
- celestial vs Carrollian — two candidates for flat holography
- exact dual structure incomplete — active research
- 2024 Donnay-Bagchi integration attempt

**Unsolved (self-identified)**:
- **The exact dual of the celestial CFT**: exact definition of the 2D non-unitary CFT
- **The 4D mapping of the central charge $c$**: how is it derived from bulk gravity?
- **Carrollian vs celestial**: the true dual of flat holography?
- **The infinite tower of conformally soft theorems**: are all of them *new conformal symmetries*?
- **Exact integration of Celestial CFT + Soft hair**: completion of the sharpening of 25
- **The celestial answer to the BH information paradox**: HPS 2016 follow-up, in progress
- **The dS celestial cousin**: 28 + 29 integrated formalization
- **Resolution of the Strominger non-unitarity problem**: is 4D unitary → 2D non-unitary a contradiction?

## Historical context

**Act I — The IR Triangle of Soft theorem, BMS, Memory (1962~2014)**:
- 1962 Bondi-Metzner-Sachs — BMS group ($\mathcal I^\pm$)
- 1965 **Weinberg** — soft graviton theorem
- 1981 Ashtekar — null infinity asymptotics
- 1990s Christodoulou-Klainerman — the mathematics of the memory effect
- **2014 Strominger** — *On BMS supertranslations and Weinberg's soft graviton theorem* (arXiv:1401.7026) — formal start of the IR Triangle
- 2014 Strominger-Zhiboedov — memory effect = BMS supertranslation
- **Decisive turn**: 2014 — the IR Triangle formalized, an integration 50 years in the making

**Act II — The start of Celestial CFT (2017~2018)**:
- **2017 Pasterski-Shao** — *Conformal basis for flat space amplitudes* (arXiv:1701.00049) — **start of the celestial CFT**
- 2017 Pasterski-Shao-Strominger — celestial 4-point function
- 2017 Strominger — *Lectures on the IR Structure of Gravity and Gauge Theory* (Princeton, arXiv:1703.05448)
- 2018 Donnay-Pasterski-Puhm — *Conformally Soft Theorems and the BMS Algebra* (arXiv:1808.05483)
- 2018 Cheung-de la Fuente-Sundrum — celestial amplitude formalism
- **Decisive turn**: 2017 Pasterski-Shao — the Mellin transform is the *mathematical tool* of the celestial CFT. Formalization of the 4D ↔ 2D conversion

**Act III — Conformally soft + Carrollian + integration (2019~2022)**:
- 2019 Pate-Raclariu-Strominger — celestial OPE sharpening
- 2020 Cotler-Strominger — *The Universe as a Quantum Encoder*
- 2020 Donnay-Marteau — Carrollian boundary
- 2021 **Pasterski** — *Lectures on Celestial Amplitudes* (arXiv:2108.04801) — comprehensive review
- 2021 Bagchi — strengthening of Carrollian holography
- 2022 Costello-Paquette — celestial OPE sharpening (twistor)
- 2022 Strominger — *Lectures on the Infrared Structure of Gravity* follow-up
- **Decisive turn**: 2021 Pasterski comprehensive review — the celestial CFT settles from a one-off proposal into a *program*

**Act IV — Algebra, Cobordism, dS combination (2023~)**:
- 2023 Donnay / Fiorucci / Herfray / Ruzziconi — BMSW algebra
- 2023 Penington-Witten — *Algebras, regions, and observers* (celestial dimension)
- 2024 Donnay-Bagchi series — Carrollian-celestial integration
- 2024 Pasterski / Strominger series — synthesis of celestial holography
- 2024 Anninos / Cotler / Strominger — celestial + dS combination
- 2024~ McNamara — Cobordism + celestial
- 2025 — the celestial CFT settles as the *second external-extension* instance of the full quantum-gravity domain
- **Decisive turn**: 2024~ — the celestial CFT integrates with the IR Triangle (25) + Cobordism (24) + dS (28) + algebra (22, 26). Entry into the formal definition of *flat holography*

**Key insight**: The Celestial CFT sharpens the Soft Hair on Black Holes IR Triangle (the 50-year integration) into a *2D conformal formalism*, and formalizes the candidate *flat generalization* of the Bulk reconstruction. The **2017 Pasterski-Shao Mellin transform** is the mathematical tool of the 4D ↔ 2D conversion, and **extended BMS = Virasoro × Supertranslations** makes explicit the conformal symmetry of flat holography. The coincidence(?) that *4D Lorentz $SL(2,\mathbb C)$ = 2D global conformal $SL(2,\mathbb C)$* is the foundation of flat holography. **The second instance of an external extension of the full quantum-gravity domain** (together with Static Patch Holography dS, the two axes of quantum gravity of our universe). **Carrollian holography** (Bagchi / Donnay-Marteau 2021~) proposes an *alternative dual* — the exact relation of celestial vs Carrollian is incomplete. **HPS 2016 Soft hair** is reinterpreted as the *vacuum-sector hair* of the celestial CFT, and the flat dimension of the answer to the BH information paradox is in progress. With the 2024~ Pasterski / Strominger / Donnay synthesis, the *formal definition of flat holography* begins to settle — the flat sharpening 30 years after 1997 AdS/CFT. But the exact dual, the central charge, Carrollian vs celestial, the infinite tower of conformally soft theorems, the BH answer, dS celestial, and the non-unitarity problem remain unsolved — **the living unsolved stage of flat holography**.

## Connections

**Prior connections** (already studied):
- Soft Hair on Black Holes — **direct bridge of the IR Triangle** (the celestial CFT is the sharpening of the IR Triangle)
- Bulk reconstruction — flat reconstruction (celestial primary)
- Static Patch Holography — **flat cousin** (28 dS + 29 flat = the two axes of quantum gravity of our universe)
- Swampland — the flat dimension (No global symmetries, WGC)
- Operator Algebras in Quantum Gravity — the algebra formalism of the celestial CFT
- dS BH — the celestial dimension of flat BHs
- Quantum Field Theory — scattering amplitudes, direct use of the soft theorem
- General Relativity — null infinity, asymptotic flatness
- meta-integration — the second external extension

**Next connections** (P2 follow-ups):
- **Topological qubit non-Abelian statistics** (P2 remainder) — application-domain depth (next autonomous step)
- **6th self-check** (M22 point, MM-07 N=5 verification)
- **Microscopic identity of the inflaton field** (P2 remainder) — cosmology depth
- **LQG / CDT alternatives** (P3) — non-string quantum gravity
- **Revisit of the P3 bridge limits** (Fermi's golden rule, DFT, Dirac bracket)

**Related concepts / deep dives**:
- Twistor space + celestial CFT (Costello-Paquette)
- 4D MHV/Anti-MHV in the celestial basis
- Celestial chiral algebra
- W-algebra of soft modes
- Celestial diamonds (conformal multiplets)
- celestial entanglement
- $w_{1+\infty}$ algebra (Strominger 2021)

**arXiv / literature**:
- **Pasterski-Shao 2017** — *Conformal basis for flat space amplitudes*, arXiv:1701.00049
- Pasterski-Shao-Strominger 2017 — celestial 4-point
- Strominger 2017 — *Lectures on IR Structure*, arXiv:1703.05448
- Donnay-Pasterski-Puhm 2018 — *Conformally Soft Theorems*, arXiv:1808.05483
- **Pasterski 2021** — *Lectures on Celestial Amplitudes*, arXiv:2108.04801 — synthesis
- Donnay-Marteau 2021 — Carrollian
- Strominger 2021 — *$w_{1+\infty}$ symmetry of celestial CFT*
- Costello-Paquette 2022 — twistor + celestial
- textbooks: Pasterski celestial lectures; Strominger IR lectures; Donnay BMSW review

## Workflow Connections
- index
- physicist
- modern-README
- Soft Hair on Black Holes — BMS Algebra and the BH Interior
- Static Patch Holography — Observer-Dependent Holography in dS
- Bulk reconstruction
- Swampland
- Operator Algebras in Quantum Gravity — Type III and Modular Flow
- meta-integration
