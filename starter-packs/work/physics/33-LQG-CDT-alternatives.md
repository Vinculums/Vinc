# LQG

## One-line essence
Beyond String Theory and M-theory, the **3 major non-string quantum-gravity candidate programs** — ① **Loop Quantum Gravity (LQG)**: Ashtekar 1986 variables + spin network + spin foam (background-independent canonical quantization) ② **Causal Dynamical Triangulations (CDT)**: Loll-Ambjorn-Jurkiewicz 2004, a time-consistent, background-independent path integral ③ **Asymptotic Safety**: Weinberg 1979 + Reuter 1998 (a UV fixed point, gravity becomes definable). A *direct response* to the canonical-quantization ambiguity + Wheeler-DeWitt time problem of the Dirac bracket. An *alternative* to the AdS/CFT·String Theory·Swampland string-theory programs — a different starting point, the same goal (a consistent definition of quantum gravity). **The last of the P3 residue (since round 1)** ✅ — a *classification of alternative programs* outside the full-fledged quantum-gravity domains.

## Physical picture

**Why non-string quantum-gravity candidates are needed**:
- The crisis of string theory: the landscape ($10^{500}$, String Theory) + dS conjecture (Swampland) + testability
- *Alternative programs* offer other candidates for a consistent definition of quantum gravity
- LQG = precise formulation of canonical quantization (Dirac bracket)
- CDT = path-integral formulation (time-consistent)
- Asymptotic Safety = Weinberg 1979 RG fixed point

**Analogy — three paths up a mountain**:
- String theory = helicopter (starts in higher dimensions, M-theory landscape)
- LQG = mountaineering (no background spacetime, building up quantum geometry)
- CDT = gravel (building discrete spacetime by random walk)
- Asymptotic Safety = bungee (RG flow converges to a UV fixed point)

**Program 1: Loop Quantum Gravity (LQG)**

**Ashtekar 1986 — new variables**:
- Instead of ADM variables (General Relativity):
- $A_a^i$ = $SU(2)$ connection (gauge field)
- $E_a^i$ = densitized triad (electromagnetic-field cousin)
- GR in gauge-theory form
- $\{A_a^i, E_b^j\} = \delta_a^b\delta^{ij}\delta(x-y)$

**Wilson loop / Holonomy** (Rovelli-Smolin 1990):
- $h_\gamma[A] = \mathcal P\exp\left(\int_\gamma A\right)$
- Gauge-invariant variable
- Quantization variable = loop function

**Spin network** (Penrose 1971·Rovelli-Smolin 1995):
- Graph + an $SU(2)$ representation $j$ on each edge
- An intertwiner at each node
- *The basic representation of quantum geometry*
- Area·volume operators have a *discrete* spectrum

**Area quantization**:
$$\hat A|s\rangle = 8\pi\gamma\ell_{\rm pl}^2 \sum_e \sqrt{j_e(j_e+1)} |s\rangle$$
- $\gamma$ = Immirzi parameter
- $\ell_{\rm pl}^2 = G\hbar/c^3$
- A *minimal area unit* exists

**Spin foam** (Reisenberger-Rovelli 1997):
- Time evolution of a spin network
- The LQG cousin of the path integral
- A *precise formulation* of Wheeler-DeWitt
- EPRL model (Engle-Pereira-Rovelli-Livine 2008)

**Strengths of LQG**:
- Background independent (no background spacetime)
- 4D direct (no extra dimensions)
- Singularity resolution (BH·Big Bang)
- *Non-perturbative* definition

**Weaknesses of LQG**:
- Difficult to integrate the Standard Model + matter
- Lorentz-invariance verification incomplete (Immirzi-parameter dependence)
- Recovery of the *continuous-spacetime limit* unproven
- No direct AdS/CFT cousin

**Program 2: Causal Dynamical Triangulations (CDT)**

**Loll-Ambjorn-Jurkiewicz 2004** (arXiv:hep-th/0404156):
- Discretizes spacetime into a collection of 4-simplices
- Enforces *time-direction consistency* (causal structure)
- Path integral:
$$Z = \sum_{\rm triangulations} e^{-S_{\rm Regge}}$$
- $S_{\rm Regge}$ = Regge action (the simplex form of Einstein-Hilbert)

**Phase diagram** (Ambjorn-Jurkiewicz-Loll):
- Phase A: crumpled (singular)
- Phase B: branched polymer (1-dimensional-like)
- **Phase C: extended 4D** (de Sitter-like)
- The continuum limit is possible in phase C

**Key results**:
- *4-dimensional spacetime emerges naturally* (not imposed by hand)
- de Sitter solution directly verified
- spectral dimension scaling: 2D at short distance, 4D at long distance

**Strengths of CDT**:
- 4D direct verification
- Lorentzian (causal) natural
- Numerical simulation possible

**Weaknesses of CDT**:
- Matter integration incomplete
- Exact definition of the continuum limit incomplete
- Derivation of the Standard Model unproven

**Program 3: Asymptotic Safety**

**Weinberg 1979** — *Ultraviolet Divergences in Quantum Theories of Gravitation*:
- A general EFT appears *non-renormalizable*, but
- If a *nontrivial UV fixed point* exists it can be defined
- The RG flow converges to $g_*(\mu)$ (as $\mu \to \infty$)

**Reuter 1998** (arXiv:hep-th/9605030):
- Functional Renormalization Group (FRG)
- $\partial_t\Gamma_k = \frac{1}{2}\text{STr}\left[(\Gamma_k^{(2)} + R_k)^{-1}\partial_t R_k\right]$
- RG flow of Newton's $G$ + $\Lambda$
- Discovered a **nontrivial UV fixed point** ($G_*, \Lambda_*$)

**Fixed-point structure**:
- IR (low energy): Newton $G$, $\Lambda$
- UV (high energy): $G \to G_* \neq 0$, $\Lambda \to \Lambda_*$
- *finite UV* — quantum gravity becomes definable

**Strengths of Asymptotic Safety**:
- Keeps the QFT formalism as is (no extra structure needed)
- Natural integration of Standard Model + gravity
- Precise verification possible (RG-flow computation)

**Weaknesses of Asymptotic Safety**:
- *Proof* of fixed-point existence incomplete (only strong evidence)
- Truncation dependence
- No direct answer to BH·information paradox

**Comparison with AdS/CFT·String Theory**:

| Program | Starting point | Spacetime | UV definition | Matter |
|---|---|---|---|---|
| String theory | 1D string | 10D/11D | natural | automatic |
| AdS/CFT | boundary CFT | AdS bulk | CFT defines it | automatic |
| **LQG** | 4D canonical quantization | background independent | spin network/foam | difficult |
| **CDT** | path integral | 4D simplex | continuum limit | difficult |
| **Asymptotic Safety** | EFT + RG | 4D natural | UV fixed point | natural |

**Direct response to the Dirac bracket**:
- LQG = precise formulation of the canonical-quantization ambiguity
- CDT = path-integral response to the Wheeler-DeWitt time problem
- Asymptotic Safety = overcoming the non-renormalizable limit

**Comparison with Swampland**:
- String theory: 6 major conjectures (No global·Distance·WGC·dS·TCC·Cobordism)
- LQG: a *swampland cousin* incomplete (different starting point)
- Verification: all attempt to match our-universe quantum gravity (accelerated expansion·BH)

**Page curve information paradox**:
- String theory: replica wormholes·island formula
- LQG: BH quantum geometry, a "residual entropy" candidate (Rovelli·Vidotto)
- CDT: BH simulation underway
- Asymptotic Safety: BH can be *non-singular*

**dS combination with Static Patch Holography — Observer-Dependent Holography in dS**:
- String theory: CLPW 2022 type II$_1$
- LQG: cosmological constant natural ($\Lambda > 0$ possible)
- CDT: dS phase C directly verified
- Asymptotic Safety: $\Lambda_*$ naturally appears

## Mathematical structure

### LQG

**Ashtekar variables**:
- Connection: $A_a^i = \Gamma_a^i + \gamma K_a^i$
  - $\Gamma_a^i$ = 3D spin connection
  - $K_a^i$ = extrinsic curvature
  - $\gamma$ = Immirzi parameter
- Triad: $E_a^i$ = densitized 3-bein
- Symplectic structure:
$$\{A_a^i(x), E_b^j(y)\} = 8\pi G\gamma \delta_a^b \delta^{ij}\delta^{(3)}(x-y)$$

**Constraints**:
- Gauss: $G^i = D_a E_a^i = 0$
- Diffeomorphism: $V_a = E_b^i F_{ab}^i = 0$
- Hamiltonian: $H = E_a^i E_b^j F_{ab}^k \epsilon_{ijk}/\sqrt{|E|} + \cdots$

**Holonomy**:
$$h_\gamma[A] = \mathcal P\exp\left(\int_\gamma A_a^i\tau_i dx^a\right)$$
- $\tau_i = i\sigma^i/2$ (SU(2) generator)

**Spin network state**:
- $\Gamma$ = directed graph
- $j_e$ = irreducible $SU(2)$ rep on edge $e$
- $i_v$ = intertwiner at vertex $v$
- $|\Gamma, j_e, i_v\rangle$ = spin network state

**Area operator**:
$$\hat A_S = 8\pi\gamma G\sum_{e \cap S}\sqrt{j_e(j_e+1)}$$
- Discrete spectrum
- Minimal area $\sim \gamma G$

**Volume operator** (Rovelli-Smolin):
$$\hat V_R = (\text{discrete spectrum, depending on }j_e)$$

**Spin foam amplitude** (EPRL):
$$Z = \sum_{\rm spin foams} \prod_v A_v(j_e, i_v)$$
- $A_v$ = vertex amplitude (Lorentzian)

### CDT

**Regge action**:
$$S_{\rm Regge} = \frac{1}{8\pi G}\sum_{\rm hinges} V_h \delta_h - \Lambda\sum_{\rm 4-simplex} V_{\rm 4-simplex}$$
- $V_h$ = volume of hinge
- $\delta_h$ = deficit angle

**Path integral**:
$$Z(\beta_1, \beta_2) = \sum_{\rm triangulations} \frac{1}{C(T)}e^{iS_{\rm Regge}(T)}$$
- Lorentzian → Euclidean (Wick rotation)
- Causal: only time-direction-consistent simplices

**Phase diagram**:
- $\beta_1$ (4D Newton constant) vs $\beta_2$ (cosmological)
- Phase A·B·C (extended 4D)

**Spectral dimension**:
$$d_S(\sigma) = -2\frac{d \ln P(\sigma)}{d\ln\sigma}$$
- $P(\sigma)$ = return probability of random walk
- Short distance: $d_S \to 2$
- Long distance: $d_S \to 4$

### Asymptotic Safety

**FRG equation**:
$$\partial_t \Gamma_k = \frac{1}{2}\text{STr}\left[(\Gamma_k^{(2)} + R_k)^{-1}\partial_t R_k\right]$$
- $t = \ln(k/k_0)$
- $\Gamma_k$ = effective average action
- $R_k$ = regulator

**Newton's $G$ flow**:
$$\partial_t \tilde G = (2 - \theta_G)\tilde G + \cdots$$
- Dimensionless $\tilde G = G k^2$
- Fixed point: $\tilde G_* \neq 0$

**Reuter fixed point**:
- $\tilde G_* \approx 0.7$
- $\tilde \Lambda_* \approx 0.2$
- 2 relevant directions
- Quantum gravity *appears* non-renormalizable → in reality *finite UV*

## Why it matters

**Response to the String Theory landscape crisis**:
- The $10^{500}$ problem → non-string quantum gravity may allow *uniqueness*
- The testability crisis → LQG·CDT are 4D direct
- Direct application to our-universe quantum gravity

**Direct refinement of canonical quantization via the Dirac bracket**:
- LQG = resolution of the canonical-quantization ambiguity
- Wheeler-DeWitt time problem + spin-foam path integral
- The quantum-gravity cousin of BRST·Dirac bracket

**Alternative answer to the Page curve information paradox**:
- String theory: replica + island
- LQG: quantum-geometry singularity resolution
- BH is *non-singular* — information *eternally preserved* (Rovelli·Vidotto)

**dS comparison with Static Patch Holography — Observer-Dependent Holography in dS**:
- String-theory dS = swampland?
- LQG·CDT: can handle $\Lambda$ naturally
- A different path to *our-universe quantum-gravity verification*

**Unresolved (self-identified)**:
- **The continuous-spacetime limit of LQG**: continuum limit unproven
- **Matter integration in CDT**: derivation of the Standard Model?
- **Proof of the Asymptotic Safety fixed point**: only strong evidence
- **Holography of LQG·CDT**: an AdS/CFT cousin?
- **Unification of the three programs**: different expressions of the same quantum gravity?
- **Testable signals**: unique predictions of LQG·CDT?
- **Standard Model + gravity**: non-string unification?

## Historical context

**Act 1 — the start of canonical-quantization quantum gravity (1967~1986)**:
- 1967 DeWitt — Wheeler-DeWitt equation
- 1980 Penrose — twistor + spin-network foundation
- **1986 Ashtekar** — *New variables for classical and quantum gravity* (Phys. Rev. Lett. 57)
  - $A_a^i$, $E_a^i$ — a new starting point for quantum gravity
- 1988 Jacobson-Smolin — loop variables
- 1990 Rovelli-Smolin — *Loop space representation of quantum general relativity* — the formal start of LQG
- **Decisive turning point**: 1986 Ashtekar — GR transformed into gauge-theory form, enabling canonical quantization

**Act 2 — the start of spin network·spin foam·CDT (1995~2005)**:
- 1995 Rovelli-Smolin — *Discreteness of area and volume*
- 1996 Rovelli — *Black hole entropy from loop quantum gravity*
- 1997 Reisenberger-Rovelli — spin-foam formulation
- **1998 Reuter** — *Nonperturbative evolution equation for quantum gravity* (arXiv:hep-th/9605030) — Asymptotic Safety + FRG
- 2000 Ambjorn-Jurkiewicz-Loll — Lorentzian path integral
- **2004 Ambjorn-Jurkiewicz-Loll** — *Emergence of a 4d World from Causal Quantum Gravity* — the formulation of CDT
- **Decisive turning point**: 2004 CDT — 4D spacetime *emerges naturally*. 1998 Reuter — Asymptotic Safety becomes possible at an RG fixed point

**Act 3 — spin foam EPRL + Phase C (2007~2012)**:
- 2007 Engle-Pereira-Rovelli — EPR model
- **2008 Engle-Pereira-Rovelli-Livine (EPRL)** — *LQG vertex with finite Immirzi parameter* — spin-foam formulation
- 2008~ Ambjorn-Loll — CDT phase diagram
- 2010 Rovelli-Vidotto — *Covariant Loop Quantum Gravity* (textbook)
- 2011 Litim·Reuter — refinement of Asymptotic Safety
- 2012 Vidotto — LQG cosmology
- **Decisive turning point**: 2008 EPRL — the Lorentzian formulation of spin foam. 2010 Rovelli-Vidotto textbook — the standardization of LQG

**Act 4 — holography + string-theory comparison + refinement (2013~)**:
- 2013 Bianchi-Rovelli — entanglement entropy in LQG
- 2015~ Pranzetti — entanglement + LQG
- 2016 Eichhorn — Asymptotic Safety + matter
- 2018 Bonzom-Costantino — group field theory (LQG·CDT integration)
- 2020~ Wieland — attempt at 4D LQG + holography
- 2022 Pereira·Speziale — spin-foam holography
- 2023 Eichhorn-Held — Asymptotic Safety + Standard Model
- 2024~ — *mutual comparison* of LQG·CDT·Asymptotic Safety underway
- 2025 — non-string quantum gravity settles in as an *alternative program*
- **Decisive turning point**: 2018~2024 — group field theory + non-string holography attempts settle the *string-theory comparison* of LQG·CDT

**Core insight**: LQG·CDT·Asymptotic Safety are the **3 major non-string quantum-gravity candidate programs** beyond String Theory and M-theory. The **1986 Ashtekar variables** are the starting point of LQG, refined via the **1990 Rovelli-Smolin loop representation** + **1995 spin-network quantization** + **2008 EPRL spin foam**. The **2004 Ambjorn-Jurkiewicz-Loll CDT** verified the *natural emergence* of 4D spacetime. The **1979 Weinberg + 1998 Reuter Asymptotic Safety** proposed the possibility of *finite UV* quantum gravity at an RG fixed point. A *direct response* to the canonical-quantization ambiguity·Wheeler-DeWitt time problem of the Dirac bracket. An *alternative* to the String Theory landscape crisis·testability crisis — a different starting point (4D direct·background independent·RG fixed point), the same goal (a consistent definition of quantum gravity). Yet the LQG continuum limit·CDT matter·proof of the Asymptotic Safety fixed point·holography cousin·unification of the three programs·testable signals·Standard Model integration all remain unresolved — **the living, unresolved stage of non-string quantum gravity**. **The last study of the P3 residue ✅** — the *alternative classification* of quantum gravity is completed, reaching *a genuine convergence* of the learning domain.

## Connections

**Prior connections** (already studied):
- String Theory — **object of comparison** (quantum-gravity candidates beyond string theory)
- Dirac bracket — **direct response** (LQG refinement of canonical quantization·Wheeler-DeWitt·BRST)
- General Relativity — ADM·canonical-form starting point
- Page curve — alternative answer to the BH information paradox (LQG singularity resolution)
- Static Patch Holography — Observer-Dependent Holography in dS — dS comparison
- Swampland — the non-string cousin of the 6 major string-theory conjectures, incomplete

**Next connections**:
- **Self-check round 7** (at M25, MM-07 N=6 verification)
- **BH interior reconstruction** (follow-up to 26) — LQG singularity vs replica
- **Carrollian holography** (follow-up to 29) — non-string holography
- **PHYSICS *genuine convergence*** — the point of reviewing Dohun's decision mode

**Related concepts·deep dives**:
- Group field theory (Oriti) — LQG + spin foam integration
- Causal sets (Sorkin) — Lorentzian discreteness
- Tensor models (Gurau) — random tensor + CDT
- Causal Fermion Systems (Finster)
- Hamiltonian constraint operator (Thiemann)
- Black hole entropy + LQG (Rovelli·Krasnov)

**arXiv·literature**:
- **Ashtekar 1986** — Phys. Rev. Lett. 57:2244
- Rovelli-Smolin 1990 — *Loop space representation*
- Rovelli-Smolin 1995 — *Discreteness of area and volume*
- **Reuter 1998** — *Nonperturbative evolution equation for quantum gravity*, arXiv:hep-th/9605030
- **Ambjorn-Jurkiewicz-Loll 2004** — *Emergence of a 4d World from Causal Quantum Gravity*, arXiv:hep-th/0404156
- **EPRL 2008** — *LQG vertex with finite Immirzi parameter*, arXiv:0711.0146
- Rovelli 2004·2014 — *Quantum Gravity* (Cambridge)
- Rovelli-Vidotto 2014 — *Covariant Loop Quantum Gravity* (Cambridge)
- Loll 2019 — *Quantum gravity from causal dynamical triangulations*, arXiv:1905.08669
- Eichhorn 2018 — *An asymptotically safe guide to quantum gravity*, arXiv:1810.07615
- Textbooks: Rovelli *Quantum Gravity*; Loll *CDT review*; Reuter-Saueressig *Asymptotic Safety*

## Workflow Connections
- index
- String Theory
- Dirac bracket
- General Relativity
- Static Patch Holography — Observer-Dependent Holography in dS
- Page curve
