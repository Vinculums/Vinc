# Soft Hair on Black Holes — BMS Algebra and the BH Interior

## One-line essence
**Hawking-Perry-Strominger 2016** (Hawking's last paper) applies the dS BH BMS supertranslation / Strominger soft theorems (2014) / Strominger-Zhiboedov memory effect to the BH event horizon $\mathcal H^+$ to derive *infinite-dimensional supertranslation hair* — **"weakening of the no-hair theorem + a candidate information-conservation mechanism."** Soft photons / soft gravitons activate the hair, and the 2014 Strominger **IR triangle** (soft theorem ↔ BMS ↔ memory) achieved a 50-year integration, presenting the possibility of verifying the LIGO memory effect. Because of the **Mirbabayi-Porrati / Bousso-Porrati critiques** (external degrees of freedom / negligible influence), information sufficiency is unsolved. Combination with the 2017~ celestial CFT (Pasterski-Shao) / 2019 Cobordism (McNamara-Vafa) / 2022~ Operator algebra type III algebra is underway — **the eighth full quantum-gravity note, the first instance of the *topological integration stage* of the Swampland Cobordism + 22 algebra + 23 flat/dS**.

## Physical picture

**No-hair theorem (1967~1975)**:
- degrees of freedom of a classical BH: mass $M$, angular momentum $J$, charge $Q$ — only 3
- Israel / Carter / Hawking / Robinson — uniqueness theorem
- "the BH is bald (no hair)" — all other information disappears beyond the event horizon
- direct cause of the information paradox — if there is no information in the Hawking radiation, information is lost

**HPS 2016 weakening**:
- an **infinite-dimensional supertranslation charge** exists on the event horizon $\mathcal H^+$
- supertranslation hair: the function degrees of freedom $f(\theta, \phi)$ (infinite-dimensional)
- soft photon hair / soft graviton hair activate it
- "the BH is not bald — infinitely many soft hair conserved"

**Analogy — parallel between null infinity and the event horizon**:
- $\mathcal I^+$ (null infinity): BMS supertranslation asymptotic symmetry (Bondi-Metzner-Sachs 1962)
- $\mathcal H^+$ (event horizon): the same BMS algebra acts as a residual symmetry (HPS 2016)
- **the two surfaces are parallel** — the soft theorem transfers charge between the two surfaces

**IR Triangle** (Strominger 2014~):
three things are different expressions of the same information:
- ① **Soft graviton theorem** (Weinberg 1965): $\lim_{\omega\to 0}\omega\mathcal A_{\rm soft} = (\text{soft factor})\mathcal A_n$
- ② **BMS supertranslation Ward identity**: BMS charge conservation
- ③ **Memory effect** (Strominger-Zhiboedov 2014): permanent spacetime deformation after a gravitational wave passes

**Memory effect intuition**:
- two free-falling mirrors separated by a distance $\Delta x$
- after a gravitational-wave burst passes, the distance is *permanently* deformed to $\Delta x + \delta x$
- $\delta x \sim$ BMS supertranslation transformation amount
- LIGO / LISA verifiable — a *classical measurement* of the soft graviton

**Soft hair mechanism**:
- during BH formation, soft gravitons / photons *imprint* on the event horizon
- can the Hawking radiation *emit* information through the same soft modes?
- supertranslation charge conservation: $\partial_t Q_f = 0$ ($f$ an infinite family of functions)
- infinite conserved quantities → candidate information-conservation mechanism

**Critiques**:
- **Mirbabayi-Porrati 2016** (arXiv:1607.03120): soft hair are *external* degrees of freedom of the BH. Inadequate for encoding interior information
- **Bousso-Porrati 2017**: the influence of soft hair on Hawking radiation is negligible ($\sim 1/N$)
- Strominger rebuttal (2017~): the essence is not the soft hair itself but the *related degrees of freedom* (the whole BMS algebra)
- conclusion: **information sufficiency is a living open problem**

**Combination with Cobordism (24 Swampland)**:
- BMS supertranslation = topological charge generalization
- Cobordism conjecture: $\Omega^{\rm QG}_d = 0$ — every charge is cobordism-trivial
- the topological dimension of HPS soft hair = the BH instance of Cobordism
- McNamara 2020~: soft hair = cobordism class expression

**Combination with the Operator algebra formalism**:
- BMS algebra ⊂ asymptotic operator algebra
- combination of the automorphism (modular flow) of the type III von Neumann algebra with BMS
- 2023 Witten / Penington attempt — algebra sharpening of soft hair

**Pure gauge vs physical debate**:
- if supertranslation is pure gauge → no information
- if physical → information yes
- HPS 2016: a physical part exists (Goldstone-like soft mode)
- critique: possibly all pure gauge

**Black hole interior application** (2019~):
- Almheiri-Hartman-Maldacena-Shaghoulian 2019: replica wormholes + soft hair
- the algebraic dimension of the BH interior — integration of 22 type III + soft hair
- 2024~ underway — the first instance of full quantum-gravity-domain integration

## Mathematical structure

**BMS group at $\mathcal I^+$** (Bondi-Metzner-Sachs 1962):
$$\text{BMS}_4 = \text{SO}(3,1) \ltimes \text{Supertranslations}(S^2)$$
- Lorentz × infinite-dimensional supertranslation
- supertranslation: $u \to u + T(z, \bar z)$, $T \in C^\infty(S^2)$

**Asymptotic metric (Bondi gauge)**:
$$ds^2 = -du^2 - 2du\,dr + r^2 \gamma_{z\bar z}dz\,d\bar z + (\text{subleading})$$
- $u = t - r$: retarded time
- $\mathcal I^+$: $r \to \infty$, $u$ finite
- Bondi mass aspect $m_B(u, z, \bar z)$, news tensor $C_{zz}(u, z, \bar z)$

**Supertranslation charge** (Bondi-Sachs):
$$T_f = \frac{1}{4\pi G}\oint_{S^2} d^2z \,\gamma_{z\bar z} f(z, \bar z) m_B$$
- infinite-dimensional (all $f \in C^\infty(S^2)$)
- conservation: $du T_f \neq 0$ but $T_f^{\rm in} = T_f^{\rm out}$ (in-out transfer)

**Soft graviton theorem** (Weinberg 1965):
$$\lim_{\omega\to 0}\omega \mathcal A_{n+1}(\text{soft graviton}) = S^{(0)}\cdot \mathcal A_n$$
- $S^{(0)} = \sum_i \frac{\eta_i p_i^\mu p_i^\nu \epsilon_{\mu\nu}^*}{p_i\cdot k}$
- universal soft factor

**Strominger 2014 equivalence**:
$$\text{Soft graviton theorem} \Leftrightarrow \text{BMS supertranslation Ward identity}$$
$$\langle \text{out}|[T_f, S]|\text{in}\rangle = 0$$

**Memory effect** (Strominger-Zhiboedov 2014):
$$\Delta C_{zz}(z, \bar z) = C_{zz}(u_2) - C_{zz}(u_1) = -2D_z^2 C(z,\bar z)$$
- $C(z,\bar z)$ = Goldstone-like supertranslation variable
- permanent deformation after a gravitational-wave burst passes — classicalization of the soft graviton

**HPS 2016 — event horizon residual symmetry**:
$$\delta_f g_{\mu\nu}\big|_{\mathcal H^+} \neq 0$$
- supertranslation $f$ acts nontrivially at the horizon
- BMS algebra ⊂ Diff($\mathcal H^+$) (residual symmetry)

**Soft hair charge at horizon**:
$$Q_f^{\rm soft} = \int_{\mathcal H^+} d^2z \, du \, f(z, \bar z) \cdot \partial_u C_{zz}^{\rm horizon}$$
- supertranslation conjugate at the horizon
- imprinted during BH formation

**Subleading soft + Super-rotation** (Cachazo-Strominger 2014):
- Subleading soft graviton ↔ super-rotation (Diff($S^2$))
- Extended BMS group: BMS + super-rotation
- a candidate for the conformal symmetry of the celestial CFT

**Pasterski-Shao 2017 — Celestial sphere**:
- 4D scattering amplitudes → 2D celestial sphere (Mellin transform)
- Conformal primary $\mathcal O_\Delta(z, \bar z)$
- BMS = celestial conformal symmetry (extension)

**Celestial CFT formalization (2018~)**:
- $\langle \mathcal O_{\Delta_1}\cdots\mathcal O_{\Delta_n}\rangle_{\rm CCFT}$ = 4D bulk amplitudes
- BMS Ward = CCFT conformal Ward
- a candidate 2D non-unitary CFT

**Critique quantification**:
- Bousso-Porrati 2017: soft hair entropy $\sim O(1)$ vs $S_{\rm BH} \sim A/4G$ — negligible
- Mirbabayi-Porrati 2016: soft hair are $r > r_H$ external degrees of freedom (gauge-fixing dependent)

**Operator algebra combination** (2023~ underway):
- Asymptotic algebra $\mathcal A_{\rm asymp}$ ⊂ type III$_1$
- BMS automorphism = part of the modular flow?
- crossed product with BMS → type II$_\infty$ recovery (generalization of CPW 2022)

**Cobordism + soft hair** (McNamara 2020~):
- supertranslation charge $\in$ cobordism class
- $\Omega^{\rm BMS}_4$ trivial (application of the Cobordism conjecture)
- every BH configuration connected by BMS-cobordism

## Why it matters

**Weakening of the no-hair theorem**:
- a direct challenge to the 1967~1975 uniqueness theorems
- the possibility that the BH conserves infinite-dimensional charges
- a new paradigm of the information paradox — "the BH is not bald"

**Combination with Page curve information recovery**:
- soft hair as a candidate for encoding information in the Hawking radiation
- island formula + soft mode integration attempt (2019~)
- BMS algebra application to the BH interior

**Weakening of the Hawking radiation no-hair**:
- residual symmetry of the $r_H = 2GM$ event horizon
- horizon supertranslation = soft photon/graviton charge
- the possibility that the Hawking radiation carries away supertranslation information

**IR Triangle** (Strominger 2014~):
- a 50-year integration (Weinberg 1965 ↔ BMS 1962 ↔ memory 2014)
- soft theorem / BMS / memory are three expressions of the same information
- possibility of verifying the LIGO memory effect

**Direct bridge to the dS BH flat holography**:
- celestial CFT (Pasterski-Shao 2017) = flat holography candidate
- BMS = celestial conformal symmetry
- a candidate for formalizing the flat holography boundary state that appeared in 23

**Topological combination with the Swampland Cobordism**:
- BMS supertranslation = topological charge generalization
- deep motivation with the Cobordism conjecture
- soft hair = candidate cobordism class expression

**Operator algebra formal integration**:
- BMS algebra ⊂ asymptotic operator algebra
- combination of type III + BMS modular flow
- sharpening of the 22 / 25 formalism

**Unsolved (self-identified)**:
- **Soft hair information sufficiency**: Mirbabayi-Porrati / Bousso-Porrati critiques vs Strominger rebuttal — conclusion incomplete
- **pure gauge vs physical classification**: the exact classification of supertranslation
- **LIGO memory effect quantitative verification**: possible with current LIGO sensitivity? Is LISA needed?
- **Celestial CFT exact dual**: formal definition of the non-unitary 2D CFT
- **Cobordism + soft hair topological integration**: McNamara follow-up
- **22 type III algebra + BMS combination**: Witten / Penington attempt underway
- **BH interior BMS application**: AHMS 2019~ replica wormholes + soft hair
- **Exact compatibility with the no-hair theorem**: is soft hair *real* hair, or a gauge artifact?

## Historical context

**Act I — BMS group and the No-hair theorem (1962~1975)**:
- 1962 **Bondi-Metzner-Sachs** — formalization of the BMS group at null infinity. Infinite-dimensional supertranslation
- 1965 **Weinberg** — soft graviton theorem (universal soft factor)
- 1967 Israel — Schwarzschild uniqueness (start of no-hair)
- 1971 Carter — Kerr uniqueness
- 1972 Bekenstein — BH entropy
- 1973 Bardeen-Carter-Hawking — BH four laws
- **1975 Hawking / Robinson — completion of the no-hair theorem** (3 degrees of freedom)
- 1976 Hawking — information paradox (Hawking radiation)
- **Decisive turn**: 1975 no-hair theorem — formalization of the direct cause of the information paradox. BMS proceeded separated from BHs for a while

**Act II — Formalization of the IR Triangle of Soft theorem / BMS / Memory (2014~2015)**:
- 1981 Ashtekar — null infinity asymptotic formalization
- 1990s~ Christodoulou / Klainerman — mathematical discovery of the memory effect
- 2014 **Strominger** — *On BMS supertranslations and Weinberg's soft graviton theorem* (arXiv:1401.7026) — **BMS = soft graviton Ward identity**
- 2014 He-Lysov-Mitra-Strominger — soft photon ↔ large gauge transformation
- 2014 **Strominger-Zhiboedov** — *Gravitational Memory, BMS Supertranslations and Soft Theorems* (arXiv:1411.5745) — **memory effect = BMS**
- 2014 Cachazo-Strominger — subleading soft + super-rotation
- 2015 Pasterski-Strominger-Zhiboedov — *New symmetries of massless QED*
- 2015 Hawking — *The information paradox for black holes* (Stockholm lecture)
- **Decisive turn**: 2014 Strominger — **formalization of the IR Triangle**. After 50 years, soft theorem (1965) / BMS (1962) / memory (1990s) are integrated as three expressions of the same information. LIGO memory verifiability

**Act III — Appearance of HPS 2016 Soft hair and critiques (2016~2017)**:
- **2016 Jan HPS (Hawking-Perry-Strominger)** — *Soft Hair on Black Holes* (arXiv:1601.00921, PRL 116:231301) — **supertranslation hair on the event horizon**
- 2016 May HPS — *Superrotation Charge and Supertranslation Hair on Black Holes and Soft Theorems* (arXiv:1611.09175) — follow-up, super-rotation added
- 2016 July **Mirbabayi-Porrati** — *Dressed Hard States and Black Hole Soft Hair* (arXiv:1607.03120) — **critique**: soft hair are external degrees of freedom
- 2017 **Bousso-Porrati** — *Soft hair as a soft wig* (arXiv:1706.00436) — **critique**: Hawking radiation influence negligible
- 2017 Strominger — *Lectures on the IR Structure of Gravity and Gauge Theory* (Princeton, arXiv:1703.05448) — comprehensive lectures
- 2017 Pasterski-Shao — *Conformal basis for flat space amplitudes* (arXiv:1701.00049) — **start of celestial CFT**
- **2018 Mar Hawking's death** — Soft hair is Hawking's **last paper**
- **Decisive turn**: 2016 HPS — Hawking himself first proposed a candidate answer to the information paradox. A new paradigm of *weakening of the no-hair theorem + a candidate information-conservation mechanism*. But because of the critiques, information sufficiency is unsolved

**Act IV — Celestial CFT / Cobordism / 22 algebra integration (2018~)**:
- 2018 Strominger series — synthesis of the IR Triangle
- 2019 Almheiri-Hartman-Maldacena-Shaghoulian (AHMS) — replica wormholes + island formula
- 2019 **McNamara-Vafa** — Cobordism conjecture (arXiv:1909.10355) — **topological generalization**
- 2020~ Donnay / Fiorucci / Herfray / Ruzziconi — BMS algebra sharpening (BMSW algebra)
- 2020~ Heidenreich-Reece-Rudelius — Cobordism + WGC integration
- 2021 Pasterski — comprehensive review of the celestial CFT (*Lectures on Celestial Amplitudes*)
- 2022 **CPW (Chandrasekaran-Penington-Witten)** — type III → II$_\infty$ algebra (Operator algebra)
- 2022~ Strominger-Pasterski series — synthesis of celestial holography
- 2023~ **Penington / Witten** — *Algebras, regions, and observers* — soft hair + 22 algebra combination attempt
- 2024~ **McNamara / Heidenreich-Reece-Rudelius** — Cobordism + soft hair topological integration
- 2024~ — **the integration stage of the full quantum-gravity domain is underway** (integration attempt of the 7-year accumulation of 18~25)
- **Decisive turn**: 2017~ — soft hair was elevated from a one-off proposal to an **integration program** of IR Triangle + celestial CFT + Cobordism + 22 algebra. The *topological / algebraic integration* of the full quantum-gravity domain is underway

**Key insight**: Soft hair (HPS 2016), as the **eighth note** of the full quantum-gravity domain, formalizes the first instance of the **topological / algebraic integration** of the dS BH BMS / Swampland Cobordism / Operator algebra type III algebra. Hawking's last paper (2016) *weakens* the 1975 no-hair theorem — an infinite-dimensional supertranslation charge exists on the BH event horizon, and soft photons / soft gravitons activate the hair. But because of the Mirbabayi-Porrati critique (external degrees of freedom) / Bousso-Porrati critique (negligible influence), information sufficiency is a living open problem. The **2014 Strominger IR Triangle** (soft theorem ↔ BMS ↔ memory) achieves a 50-year integration and presents the possibility of verifying the LIGO memory effect. The 2017~ celestial CFT (Pasterski-Shao) / 2019 Cobordism (McNamara-Vafa) / 2022~ 22 algebra (CPW) / 2023~ integration attempts (Penington-Witten / McNamara / Heidenreich-Reece-Rudelius) are underway — **the eighth note of the full quantum-gravity domain is the first instance of the *within-domain integration stage***. Following 22 algebra (formal tools) / 23 flat/dS (application to our universe) / 24 Swampland (constraint principles), 25 Soft hair presents a meta-integration candidate of **topological integration + IR Triangle + algebra combination**. But information sufficiency / pure gauge classification / LIGO verification / celestial dual / Cobordism+soft hair / 22+BMS combination / BH interior application / no-hair compatibility remain unsolved — **the most active integration stage of the full quantum-gravity domain**.

## Connections

**Prior connections** (already studied):
- Swampland — **direct bridge with the Cobordism conjecture** (topological combination)
- dS BH — deep motivation of BMS / flat holography / celestial CFT
- Operator algebra — type III + BMS algebra combination (2023~ underway)
- Factorization paradox — single asymptotic vs ensemble
- Replica wormholes — replica + soft hair (AHMS 2019~)
- Page curve — information recovery + soft mode encoding
- Hawking radiation — **direct instance of the weakening of the no-hair theorem**
- AdS/CFT — comparison of boundary CFT vs celestial CFT
- Quantum Information — entanglement + supertranslation
- Quantum Field Theory — direct use of the Weinberg 1965 soft theorem
- General Relativity — direct use of Schwarzschild / Kerr / null infinity
- Special Relativity — flat spacetime asymptotics, $\mathcal I^\pm$

**Next connections** (P2 new follow-ups):
- **Bulk reconstruction algebra** — the first instance of the domain integrating the 22 / 23 / 24 / 25 formalism
- **Static patch holography** — the BMS dimension of Susskind dS
- **Celestial CFT depth** — Pasterski-Shao follow-up, arising naturally in 25
- **Full quantum-gravity-domain integration application** — meta-review of the 8 items 18~25
- **5th self-check** — M19 accumulation point, additional MM-07 verification
- **LQG / CDT alternatives** (P3) — BMS generalization in non-string quantum gravity

**Related concepts / deep dives**:
- BMSW algebra (BMS + Weyl) — Donnay-Fiorucci-Herfray-Ruzziconi 2020~
- Subleading soft theorem (Cachazo-Strominger 2014) — super-rotation
- Asymptotic flat solutions — Strominger lectures 2017
- Conformal soft theorem (Donnay-Pasterski-Puhm)
- Carrollian holography (Bagchi / Donnay-Marteau 2021~)
- Black hole entropy + soft mode (Strominger / Donnay 2024~)
- Replica + soft hair — AHMS 2019, Penington-Witten 2023~

**arXiv / literature**:
- **HPS 2016** — *Soft Hair on Black Holes*, PRL 116:231301, arXiv:1601.00921
- HPS 2016 follow-up — *Superrotation Charge and Supertranslation Hair*, arXiv:1611.09175
- **Strominger 2014** — *On BMS supertranslations and Weinberg's soft graviton theorem*, arXiv:1401.7026
- **Strominger-Zhiboedov 2014** — *Gravitational Memory, BMS Supertranslations and Soft Theorems*, arXiv:1411.5745
- **Mirbabayi-Porrati 2016** — *Dressed Hard States and Black Hole Soft Hair*, arXiv:1607.03120
- **Bousso-Porrati 2017** — *Soft hair as a soft wig*, arXiv:1706.00436
- **Strominger 2017** — *Lectures on the IR Structure of Gravity and Gauge Theory*, arXiv:1703.05448
- **Pasterski-Shao 2017** — *Conformal basis for flat space amplitudes*, arXiv:1701.00049
- Pasterski 2021 — *Lectures on Celestial Amplitudes*, arXiv:2108.04801
- **McNamara-Vafa 2019** — *Cobordism Conjecture, Anomalies, and the Swampland*, arXiv:1909.10355
- AHMS 2019 — *The Page curve of Hawking radiation from semiclassical geometry*, arXiv:1908.10996
- Penington-Witten 2023 — *Algebras, regions, and observers*, arXiv:2303.02837
- textbooks: Strominger *Lectures on the IR Structure*; Pasterski lectures; Donnay-Fiorucci-Herfray-Ruzziconi BMSW review

## Workflow Connections
- index
- physicist
- modern-README
- Swampland
- dS BH
- Operator algebra
- Page curve
- Hawking radiation
- General Relativity
- Quantum Field Theory
