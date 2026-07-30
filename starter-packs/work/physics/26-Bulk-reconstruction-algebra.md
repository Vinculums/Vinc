# Bulk reconstruction

## One-line essence
The program of reconstructing AdS/CFT bulk operators as boundary CFT operators — the flow of **HKLL 2006** smearing function → **FLM 2013** RT area + quantum corrections → **Almheiri-Dong-Harlow 2015** code subspace → **JLMS 2016** boundary modular Hamiltonian = bulk + area term → **Penington-Witten 2022 (CPW)** type III → II crossed product → **Akers-Penington 2021** entanglement wedge + quantum corrections *directly integrates* the Operator algebra formalism + the dS BH flat/dS generalization limits + the Swampland quantum-gravity consistency conditions + the Soft hair on Black Holes BMS algebra integration — **the ninth note of the full quantum-gravity domain, the second verification of the U-13 stage-4 integration evolution (N=1→2 sharpening formal-registration trigger)**. It formalizes via algebra that Entanglement wedge ⊃ Causal wedge (Wall 2014), QES (Engelhardt-Wall 2014), the island formula, and the HaPPY code are *different expressions of the same mechanism*.

## Physical picture

**Core question**:
If AdS/CFT says bulk quantum gravity = boundary CFT, **which operator of the boundary CFT is the bulk operator $\Phi(X)$?**
- Is the answer *unique*?
- How far into the bulk is *reconstruction possible*?
- How is the exact mapping formalized?

**3 wedges**:
- **Causal wedge** $\mathcal C[A]$: the bulk region reachable from the boundary region $A$ by *classical signals*
- **Entanglement wedge** $\mathcal E[A]$: the bulk region enclosed by the RT/QES surface of $A$ (homological)
- **Wall 2014**: $\mathcal E[A] \supseteq \mathcal C[A]$ — in general the entanglement one is larger
- **Subregion duality**: the boundary information of $A$ = the bulk information of $\mathcal E[A]$

**Analogy — shadow and territory**:
- Causal wedge = shadow (the directly visible part)
- Entanglement wedge = territory (the part quantum information reaches)
- territory > shadow — quantum entanglement provides extra information

**HKLL 2006 — Smearing function**:
- free scalar field $\Phi(X)$ of AdS$_{d+1}$
- Boundary CFT primary operator $\mathcal O(x)$
- **HKLL kernel** $K(X|x)$:
$$\Phi(X) = \int_{\partial \text{AdS}} K(X|x) \mathcal O(x) \, d^dx$$
- $K$ is derived from the (spacetime) Green function of AdS
- propagation to the AdS rigid boundary

**Limit — confined to the causal wedge**:
- HKLL works only when the bulk operator is inside the causal wedge
- inside a bulk BH? — inaccessible by boundary signals (outside the causal wedge)
- answer: **extension to the entanglement wedge is needed**

**FLM 2013, 2018 — RT area + quantum corrections**:
- AdS/CFT Ryu-Takayanagi: $S(A) = A_{\rm RT}/4G$
- FLM quantum corrections:
$$S(A) = \frac{A_{\rm RT}}{4G_N} + S_{\rm bulk}(\Sigma_{\rm bulk}) + O(G_N)$$
- $\Sigma_{\rm bulk}$ = the bulk region inside the entanglement wedge
- the bulk entanglement entropy is the additional term

**Engelhardt-Wall 2014 — QES (Quantum Extremal Surface)**:
- $S_{\rm gen}(\gamma) = A(\gamma)/4G + S_{\rm bulk}(\Sigma_\gamma)$
- change $\gamma$ to a *quantum extremal surface* (one step beyond FLM)
- $\delta S_{\rm gen}/\delta \gamma = 0$
- the foundation of the island formula (Page curve)

**Almheiri-Dong-Harlow 2015 — Code subspace**:
- arXiv:1411.7041 *Bulk Locality and Quantum Error Correction*
- Bulk Hilbert space ≃ the *code subspace* of the boundary CFT
- Bulk operator = the *logical operator* of the boundary code
- the **quantum error-correcting code** structure is built into AdS/CFT itself
- the HaPPY code (Pastawski-Yoshida-Harlow-Preskill 2015) is an explicit instance
- "bulk information can be reconstructed from any boundary region" = quantum erasure correction

**JLMS 2016 — Modular Hamiltonian equivalence**:
- arXiv:1512.06431 *Relative entropy equals bulk relative entropy*
- Boundary modular Hamiltonian:
$$K_{\rm CFT}^A = \frac{A_{\rm RT}}{4G_N} + K_{\rm bulk}^{\Sigma} + (\text{matter, gravity dressings})$$
- the modular flows of the bulk and boundary are *the same*
- a *direct formalization* of entanglement wedge reconstruction

**Akers-Penington 2021 — Quantum corrections**:
- arXiv:2008.03319, 2110.05497
- entanglement wedge reconstruction works including *quantum corrections*
- integrated with the island formula of the Page curve
- non-perturbative effects (replica wormholes) also enter the entanglement wedge naturally

**Operator algebra CPW 2022 — Algebra sharpening**:
- $\mathcal A_{\rm bulk}$ = type III$_1$ (large $N$ limit)
- Crossed product with modular flow → type II$_\infty$
- **Generalized entropy = trace of the type II algebra**:
$$S_{\rm gen} = \tau(-\rho \ln \rho) = \frac{A}{4G} + S_{\rm matter}$$
- Bulk reconstruction = the boundary modular-flow representation of the **affiliated operators** of the type III bulk algebra

**dS BH generalization limits**:
- HKLL depends on the AdS rigid boundary — absent in flat/dS
- celestial CFT (25 IR Triangle) + bulk reconstruction = the flat case is in progress
- dS observer algebra (CLPW 2022) → static patch reconstruction attempt

**Swampland consistency conditions**:
- swampland conjectures constrain the *allowed EFTs* of bulk reconstruction
- No global symmetries → bulk reconstruction does not conserve global charges
- Distance conjecture → reconstruction breaks at moduli limit points
- WGC → BH stability ↔ bulk operator spectrum

**Soft hair on Black Holes BMS algebra integration**:
- BMS supertranslation → asymptotic algebra
- the BMS residual symmetry of the event horizon is the *topological dimension* of bulk reconstruction
- 26 combines the 25 IR Triangle at the algebra level

**Stage-4 integration evolution (second verification of U-13)**:
- 25 Soft hair = *first instance of the integration stage* (self-stated)
- 26 Bulk reconstruction algebra = *second instance of the integration stage* (direct integration of HKLL, FLM, JLMS, CPW, Akers-Penington)
- N=1 → N=2 reached, the U-13 stage-4 evolution sharpening formal-registration trigger is met

## Mathematical structure

**HKLL smearing** (free scalar in AdS$_{d+1}$):
$$\Phi(t, r, \Omega) = \int dt' d\Omega' \, K(t, r, \Omega | t', \Omega') \mathcal O(t', \Omega')$$
- $K$ = the inverse of the AdS bulk-to-boundary propagator
- Boundary primary $\mathcal O$ scaling dimension $\Delta$
- Bulk mass: $m^2 R^2 = \Delta(\Delta - d)$

**Causal wedge**:
$$\mathcal C[A] = J^+(D[A]) \cap J^-(D[A])$$
- $D[A]$ = boundary domain of dependence
- $J^\pm$ = causal future/past in bulk

**Entanglement wedge**:
$$\mathcal E[A] = D[\Sigma_A]$$
- $\Sigma_A$ = bulk Cauchy slice bounded by $A \cup \gamma_A$
- $\gamma_A$ = RT/QES surface

**Wall theorem 2014**:
$$\mathcal E[A] \supseteq \mathcal C[A]$$
- generally strict (when the entanglement is nontrivial)

**RT formula** (Ryu-Takayanagi 2006):
$$S(A) = \min_\gamma \frac{A(\gamma)}{4G_N}$$
- min over $\gamma$ homologous to $A$, anchored at $\partial A$

**FLM 2013 quantum corrections**:
$$S(A) = \min_\gamma \left[\frac{A(\gamma)}{4G_N} + S_{\rm bulk}(\Sigma_\gamma)\right] + O(G_N)$$

**QES (Engelhardt-Wall 2014)**:
$$\gamma_{\rm QES} = \text{arg ext}_\gamma S_{\rm gen}(\gamma)$$
$$S_{\rm gen}(\gamma) = \frac{A(\gamma)}{4G_N} + S_{\rm bulk}(\Sigma_\gamma)$$
- QES = quantum extremal surface (vs the FLM classical extremal)

**Code subspace** (Almheiri-Dong-Harlow 2015):
$$\mathcal H_{\rm code} \subset \mathcal H_{\rm CFT}$$
- $\mathcal H_{\rm code}$ ≃ bulk QFT Hilbert space
- Bulk operator $O_{\rm bulk}$ acts within $\mathcal H_{\rm code}$
- Logical operator structure (quantum error correction)

**HaPPY code** (Pastawski-Yoshida-Harlow-Preskill 2015):
- Tensor network on hyperbolic tiling
- Bulk index = "logical" qubit
- Boundary index = "physical" qubit
- Erasure correction ↔ AdS holography

**JLMS 2016 modular Hamiltonian**:
$$K_{\rm CFT}^A = \frac{\hat A_{\rm RT}}{4 G_N} + K_{\rm bulk}^{\Sigma_A} + (\text{boundary terms})$$
- $\hat A_{\rm RT}$ = area operator (quantum)
- the two modular flows are equivalent → an arbitrary bulk operator inside the entanglement wedge can be reconstructed with the boundary modular Hamiltonian

**Subregion duality**:
$$\mathcal A_{\rm bulk}(\mathcal E[A]) \leftrightarrow \mathcal A_{\rm CFT}(A)$$
- bulk algebra of the entanglement wedge ≃ boundary algebra of $A$
- an *isomorphism* (large $N$, code subspace)

**Akers-Penington reconstruction**:
- *every* bulk operator in the entanglement wedge is reconstructible (including quantum corrections)
- replica trick + island formula → non-perturbative entanglement wedge
- Petz map generalization (approximate reconstruction)

**Petz map** (the quantitative tool of entanglement wedge reconstruction):
$$\mathcal R_\rho(\sigma) = \rho^{1/2} \mathcal N^*(\mathcal N(\rho)^{-1/2} \sigma \mathcal N(\rho)^{-1/2}) \rho^{1/2}$$
- $\mathcal N$ = encoding map (bulk → boundary)
- $\mathcal R$ = approximate inverse (decoding)

**Operator algebra CPW 2022**:
- Bulk algebra $\mathcal A_{\rm bulk}$ in the large $N$ limit:
$$\mathcal A_{\rm bulk} = \text{type III}_1 \text{ factor}$$
- Crossed product with modular flow:
$$\mathcal A_{\rm bulk} \rtimes_\sigma \mathbb R = \text{type II}_\infty$$
- Trace $\tau$ on type II$_\infty$:
$$\tau(-\rho \ln \rho) = S_{\rm gen} = \frac{A}{4G} + S_{\rm matter}$$

**The integrated formalization of 26**:
- HKLL → causal wedge reconstruction (causal precursor)
- FLM → entanglement wedge area term
- ADH → code subspace structure
- JLMS → modular flow equivalence (subregion duality)
- QES → island formula foundation
- CPW → algebra sharpening (type III → II)
- Akers-Penington → quantum corrections integration
- **all of these are *different expressions of the same mechanism*** — the domain integration stage

## Why it matters

**Sharpening of the AdS/CFT dictionary**:
- the exact mapping of "bulk = boundary"
- Subregion duality strengthens the dictionary at the *region level*
- the first formalization of a precise definition of quantum gravity (continually evolving since 1997 Maldacena)

**Foundation of the Page curve island formula**:
- QES (Engelhardt-Wall 2014) is the direct tool of the island formula
- the disconnected component of the entanglement wedge = the island
- quantification of the BH information-recovery mechanism

**Derivation of Replica wormholes**:
- replica trick + entanglement wedge → wormhole saddles natural
- non-perturbative effects enter the entanglement wedge naturally
- the core of Akers-Penington 2021

**Direct application of Operator algebra**:
- the CPW 2022 algebra formalism is the *precise definition* of bulk reconstruction
- type III → II crossed product = the definition of generalized entropy
- the first full application instance of the 22 formalism

**dS BH generalization limits**:
- HKLL depends on the AdS boundary — absent in flat/dS
- celestial CFT + bulk reconstruction = the flat case in progress
- dS static patch reconstruction = quantum gravity of our universe

**Swampland consistency conditions**:
- swampland conjectures constrain the *allowed EFTs* of bulk reconstruction
- No global symmetries, Distance, WGC act directly on reconstruction

**Soft hair on Black Holes BMS integration**:
- the event-horizon BMS residual symmetry is the topological dimension of bulk reconstruction
- IR Triangle (soft theorem, BMS, memory) + algebra combination
- BH interior reconstruction underway

**The quantum-gravity meaning of quantum error correction**:
- "bulk locality emerges from boundary error correction"
- AdS/CFT itself is a quantum error-correcting code
- deep motivation of Holography ⟺ Quantum information

**Unsolved (self-identified)**:
- **Exact mechanism of entanglement wedge vs causal wedge**: why is the entanglement one larger, and *why must it be*?
- **Flat/dS generalization of bulk reconstruction**: how to formalize when the boundary is absent?
- **Exact dimension of the code subspace**: sharpening of $\dim \mathcal H_{\rm code} \sim e^{S_{BH}}$
- **Non-perturbative bulk operators**: other saddles besides the replica wormhole?
- **Soft hair + bulk reconstruction integration**: BMS algebra and the entanglement wedge
- **Cobordism + bulk reconstruction**: topological consistency
- **Black hole interior reconstruction**: AHMS 2019 follow-up, in progress
- **Exact limits of the Petz map**: approximate vs exact reconstruction

## Historical context

**Act I — HKLL and the Causal wedge (2006~2012)**:
- 1997 Maldacena — AdS/CFT
- 1998 Witten / GKP — operator dictionary (generating function)
- 1999 Banks-Douglas-Horowitz-Martinec — first recognition of the bulk-operator ambiguity
- **2006 HKLL** (Hamilton-Kabat-Lifschytz-Lowe) — *Holographic representation of local bulk operators* (arXiv:hep-th/0606141, 0612053)
  - boundary smearing function of the bulk scalar
  - $\Phi(X) = \int K(X|x) \mathcal O(x)$
- 2009 Heemskerk-Marolf-Polchinski-Sully — the *causal wedge* confinement of the bulk operator (arXiv:0903.4437)
- 2012 Czech-Karczmarek-Nogueira-Van Raamsdonk — start of the *entanglement wedge* definition (arXiv:1204.1330)
- **Decisive turn**: 2006 HKLL — the first formalization of the boundary representation of the bulk operator. But the limitation of being confined to the causal wedge was first recognized

**Act II — RT formula and the Entanglement wedge (2013~2015)**:
- 2006 Ryu-Takayanagi — entanglement entropy formula (AdS/CFT)
- **2013 Faulkner-Lewkowycz-Maldacena (FLM)** — *Quantum corrections to holographic entanglement entropy* (arXiv:1307.2892)
  - $S = A/4G + S_{\rm bulk}$
- **2014 Wall** — *Maximin Surfaces, and the Strong Subadditivity of the Covariant Holographic Entanglement Entropy* (arXiv:1211.3494)
  - formal proof of **entanglement wedge ⊃ causal wedge**
  - Wall theorem
- **2014 Engelhardt-Wall** — *Quantum Extremal Surfaces* (arXiv:1408.3203)
  - QES = quantum extremal surface
  - extremize $S_{\rm gen}(\gamma)$
- 2014 Headrick-Hubeny-Lawrence-Rangamani — covariant entanglement
- **2015 Almheiri-Dong-Harlow (ADH)** — *Bulk Locality and Quantum Error Correction* (arXiv:1411.7041)
  - the **code subspace** concept
  - bulk Hilbert space ≃ boundary code subspace
- **2015 Pastawski-Yoshida-Harlow-Preskill (HaPPY)** — *Holographic quantum error-correcting codes* (arXiv:1503.06237)
  - HaPPY code — explicit tensor network
- 2015 Dong-Harlow-Wall — *Reconstruction of Bulk Operators within the Entanglement Wedge in Gauge-Gravity Duality*
- **Decisive turn**: 2014~2015 — it was established that the entanglement wedge is the *true region* of reconstruction. Wall + ADH + HaPPY settled the quantum-error-correction paradigm

**Act III — JLMS, QES, Page curve (2015~2020)**:
- **2016 JLMS** (Jafferis-Lewkowycz-Maldacena-Suh) — *Relative entropy equals bulk relative entropy* (arXiv:1512.06431)
  - **boundary modular Hamiltonian = bulk + area term**
  - direct formalization of subregion duality
- 2017 Faulkner — bulk operator reconstruction sharpening
- 2017 Czech-Lamprou-Tatar — entanglement wedge + tensor network
- 2018 Cotler-Kanata-Penington — entanglement wedge in JT gravity
- **2019 Penington** + **AHMS** (Almheiri-Hartman-Maldacena-Shaghoulian) — *Page curve* island formula (arXiv:1908.10996, 1911.12333)
  - QES + entanglement wedge → BH information recovery
  - direct application of Page curve / Replica wormholes
- 2020 Penington-Shenker-Stanford-Yang — *Replica wormholes and the entropy of Hawking radiation*
- **Decisive turn**: 2016 JLMS — the modular Hamiltonian equivalence is a *direct formalization* of entanglement wedge reconstruction. With the 2019~ Page curve application, the key tool of the partial resolution of the BH information paradox

**Act IV — Algebra integration and Quantum corrections (2021~)**:
- **2021 Akers-Penington** — *Entanglement wedge reconstruction with quantum corrections* (arXiv:2110.05497, 2008.03319)
  - entanglement wedge including quantum corrections
- 2021 Penington-Shenker-Stanford-Yang — replica wormholes sharpening
- 2021 Leutheusser-Liu — *Causal structure of operator algebras* (arXiv:2110.05497)
  - bulk algebra type II/III conversion
- **2022 CPW** (Chandrasekaran-Penington-Witten) — *Large N algebras and generalized entropy* (arXiv:2209.10454)
  - **type III$_1$ → II$_\infty$ crossed product**
  - generalized entropy = type II trace
  - direct bridge to Operator algebra
- 2023 Witten — *Algebras, regions, and observers*
- 2023 Penington-Witten — *Algebras and States in JT Gravity*
- 2024~ — bulk reconstruction algebra settles as the *integration stage* of the full quantum-gravity domain
- 2024~ Akers-Levine-Wall-Zhao — non-perturbative definition of the entanglement wedge
- 2024~ Czech-Cohen-Faulkner — bulk reconstruction + soft hair + Cobordism
- **Decisive turn**: 2022 CPW — bulk reconstruction *fully sharpened* in the von Neumann algebra formalism. The type III → II crossed product defines generalized entropy as an algebra trace. The first full application of the Operator algebra formalism

**Key insight**: Bulk reconstruction algebra is the **ninth note** of the full quantum-gravity domain and the **second instance of the integration stage** following Soft hair on Black Holes — it formalizes that the flow of HKLL (2006), FLM (2013), ADH/HaPPY (2015), QES (2014), JLMS (2016), CPW (2022), and Akers-Penington (2021) are *different expressions of the same mechanism*. It functions as the **direct integration stage** of the **22 algebra formalism, 23 flat/dS generalization, 24 Swampland consistency, and 25 BMS algebra**, verifying the U-13 stage-4 integration-evolution candidate as N=1→2 (Soft hair on Black Holes first instance → 26 second instance). **Subregion duality** (Wall 2014 entanglement wedge ⊃ causal wedge + JLMS 2016 modular Hamiltonian equivalence) is the key result, and the ADH 2015 paradigm that **bulk locality emerges from boundary quantum error correction** formalizes the quantum-information essence of holography. That generalized entropy is defined as a type II trace at the CPW 2022 algebra level integrates the Page curve island formula and the Replica wormholes non-perturbative effects with algebraic precision. But the exact mechanism of entanglement wedge vs causal wedge, the flat/dS generalization, the exact dimension of the code subspace, non-perturbative bulk operators, soft hair integration, Cobordism combination, BH interior reconstruction, and the Petz map limits remain unsolved — **the *integration stage* of the full quantum-gravity domain is a living open problem**.

## Connections

**Prior connections** (already studied):
- AdS/CFT — **direct bridge** (bulk reconstruction is the formalization of the dictionary)
- Operator algebra — **the first full application of the CPW 2022 algebra formalism**
- Soft hair on Black Holes — following the *first instance of the integration stage*, 26 is the second (U-13 stage-4 evolution verification)
- Swampland — quantum-gravity consistency conditions constrain reconstruction
- dS BH — flat/dS generalization limits (boundary absent)
- Replica wormholes — replica + entanglement wedge (Akers-Penington)
- Page curve — QES + island formula (entanglement wedge application)
- Hawking radiation — entanglement entropy + Hawking radiation
- Quantum Information — quantum error correction (HaPPY code)
- Quantum Field Theory — modular Hamiltonian (Bisognano-Wichmann)
- General Relativity — Cauchy slice, causal structure
- Quantum Mechanics — entanglement, density matrix

**Next connections** (P2 new follow-ups):
- **5th self-check** (M18 point, MM-07 N=4 verification — M7→M10→M14→M16→M18 intervals 3, 4, 2, 2)
- **Static patch holography** — Susskind / Banks follow-up, dS reconstruction
- **Celestial CFT depth** — Pasterski-Shao follow-up, flat reconstruction
- **Full quantum-gravity-domain integration application** — meta-review of the 9 items 18~26
- **LQG / CDT alternatives** (P3) — reconstruction in non-string quantum gravity

**Related concepts / deep dives**:
- Petz map (the quantitative tool of entanglement wedge reconstruction)
- Modular nuclearity (Buchholz-Wichmann)
- Tomita-Takesaki theory (the algebra dimension of the modular Hamiltonian)
- Holographic complexity (Susskind, Stanford-Susskind)
- Tensor network ↔ AdS (HaPPY, MERA)
- Subregion-subregion duality
- Approximate quantum error correction (Hayden-Penington)

**arXiv / literature**:
- **HKLL 2006** — *Holographic representation of local bulk operators*, arXiv:hep-th/0606141, 0612053
- Heemskerk-Marolf-Polchinski-Sully 2009 — arXiv:0903.4437
- **FLM 2013** — *Quantum corrections to holographic entanglement entropy*, arXiv:1307.2892
- **Wall 2014** — *Maximin Surfaces*, arXiv:1211.3494
- **Engelhardt-Wall 2014** — *Quantum Extremal Surfaces*, arXiv:1408.3203
- **ADH 2015** — *Bulk Locality and Quantum Error Correction*, arXiv:1411.7041
- **HaPPY 2015** — *Holographic quantum error-correcting codes*, arXiv:1503.06237
- **JLMS 2016** — *Relative entropy equals bulk relative entropy*, arXiv:1512.06431
- Dong-Harlow-Wall 2016 — entanglement wedge reconstruction
- Penington 2019 — *Entanglement Wedge Reconstruction and the Information Paradox*
- AHMS 2019 — *The entropy of bulk quantum fields and the entanglement wedge of an evaporating black hole*
- **Akers-Penington 2021** — *Quantum minimal surfaces from quantum error correction*, arXiv:2110.05497
- **CPW 2022** — *Large N algebras and generalized entropy*, arXiv:2209.10454
- Witten 2023 — *Algebras, regions, and observers*, arXiv:2303.02837
- textbooks: Harlow *TASI Lectures on the Emergence of Bulk Physics in AdS/CFT*; Penington-Witten *Lectures on Algebras*

## Workflow Connections
- index
- physicist
- modern-README
- AdS/CFT
- Operator algebra
- Soft hair on Black Holes
- Swampland
- dS BH
- Replica wormholes
- Page curve
- Quantum Information
