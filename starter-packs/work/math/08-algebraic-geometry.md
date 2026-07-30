# Algebraic geometry

## One-line essence
As *the zero set of polynomials* — the simplest of objects — becomes the *very language* of rings, sheaves, and models, it becomes the most central integrating stage of mathematics, where all the tools of Foundations 1, 2, 3, and 4 operate at once.

## Intuitive picture

Algebraic geometry (AG) began with *the zeros of polynomials*. The solution set of an equation like $y^2 = x^3 + ax + b$ — an *algebraic variety*. Why this simple object becomes the stage on which *every tool of mathematics* acts simultaneously is the essence of this field's four acts.

Three scenes:

- **Italian school intuition (1880s–1930s)**: Castelnuovo, Enriques, and Severi developed surface classification, Riemann-Roch, and sophisticated geometric intuition. But there was *no formal foundation* — the limitation that many theorems *depended on intuition*.
- **Grothendieck 1957–1973 — refoundation**: at 26, Grothendieck introduced the concept of the *scheme*. The variety was generalized to the *spectrum of a ring*. The tens of thousands of pages of EGA (Éléments de géométrie algébrique) + SGA (Séminaire) *refounded algebraic geometry*. Every intuition of the Italian school was refined into formal tools. Fields 1966.
- **Lurie, Scholze 2010s — ∞-categories, perfectoid**: derived algebraic geometry (DAG, Toën-Vezzosi), motivic cohomology (Voevodsky), the perfectoid space (Scholze), and the ∞-topos (Lurie) are the *21st-century form* of this field. Number theory, geometry, algebra, analysis, and categories are all used *simultaneously*. **The most future-facing dimension of mathematics** (Scholze Fields 2018, at 30).

The meta-insight of this field: **AG = the stage where all the tools of Foundations 1, 2, 3, and 4 operate *simultaneously* + the serious integrating domain of number theory, geometry, algebra, analysis, categories, and ∞-categories**. A position *equal to* the 9 serious quantum-gravity items of PHYSICS.

## Formal structure

### Varieties and rings

**Affine variety** $V \subseteq \mathbb{A}^n$ (affine space):
$$V(I) = \{p \in k^n : f(p) = 0 \text{ for all } f \in I\}$$

**Coordinate ring**: $k[V] = k[x_1, \ldots, x_n] / I(V)$.

**Hilbert Nullstellensatz** (1893): $k$ algebraically closed → $I(V(J)) = \sqrt{J}$ (a 1:1 correspondence between ideals and varieties).

→ An exact bridge *geometry (variety) ↔ algebra (ideal, ring)*.

### The scheme — Grothendieck's generalization

**Affine scheme** $\text{Spec}(A)$:
- Points = the *prime ideals* of $A$
- Topological space = the Zariski topology
- The structure sheaf $\mathcal{O}_{\text{Spec}(A)}$ — using localization

**Scheme**: a ringed space locally an affine scheme.

**Generalizations**:
- Geometry over *arbitrary rings* (integer-coefficient, p-adic, differential rings, etc.)
- Number theory (number fields, local fields) enters naturally
- $\text{Spec}(\mathbb{Z})$ is the "geometric base" of number theory

**Why Grothendieck generalized**: if one handles only varieties, *number theory (integers), arithmetic geometry (p-adic), and motives* do not enter. The scheme turns *every ring* into geometry — a *refoundation*.

### Sheaves and sheaf cohomology

**Sheaf** $\mathcal{F}$ on $X$: an abelian group for each open set + restriction maps + local-global consistency.

**Sheaf cohomology** $H^i(X, \mathcal{F})$: the measurement of the loss of local → global information. Two definitions — Čech and derived functor.

**Riemann-Roch** (Hirzebruch 1954, Grothendieck's generalization 1957):
$$\chi(\mathcal{F}) = \int_X \text{ch}(\mathcal{F}) \cdot \text{td}(X)$$

→ The direct starting point of Atiyah-Singer 1963 (Geometry §Act 3).

### Étale cohomology (1960s)

**Problem**: in characteristic $p > 0$, *Zariski cohomology* lacks information.

**Étale topology**: a topology finer than Zariski (an étale morphism = effectively a *finite cover*).

**Étale cohomology** $H^i_{\text{ét}}(X, \mathbb{Q}_\ell)$: $\ell$-adic coefficients — the tool of the Weil conjectures.

**Deligne 1974, the proof of the Weil conjectures**: étale cohomology + the general Riemann hypothesis (Fields 1978).

### Motives — Grothendieck's vision

**Motive**: the "*universal cohomology*" of a variety. Every cohomology theory (Betti, de Rham, étale, crystalline) is *functorially derived* from the motive.

**Grothendieck's conjecture**: the *motivic Galois group* — algebraic cycle = Hodge class (consistent with the Hodge conjecture).

**Voevodsky 1990s–2000s** — the formalization of *motivic homotopy theory* (Fields 2002). The first integration of *homotopy theory + algebraic geometry* in the history of mathematics.

### Derived algebraic geometry (DAG)

**Toën-Vezzosi 2005** — derived stacks, algebraic geometry over ∞-categories.

**Objects**: instead of ordinary schemes, *simplicial commutative rings* / $E_\infty$-rings.

**Key tools**: ∞-categories, ∞-topos, model categories, dg-categories.

**Applications**:
- The refinement of the *cotangent complex and deformation theory*
- *Intersection theory* in all dimensions
- The mathematical foundation of *quantum cohomology*

### The perfectoid space (Scholze 2012)

**Perfectoid field** $K$: $K$ is a p-adic field of characteristic 0 that behaves as if it had a *Frobenius lift*.

**Tilting equivalence**: $K^\flat$ of characteristic $p$ — perfectoid space ↔ characteristic $p$ scheme (Scholze 2012).

**Applications**:
- The refinement of *p-adic Hodge theory*
- A new proof of *local Langlands GL_n*
- *Fargues-Scholze 2021* geometric local Langlands

→ The *most active 21st-century progress* of this field.

### The ∞-topos (Lurie 2009)

**∞-topos**: the *∞-categorical* generalization of the classical topos. All homotopy theory is *sheaf cohomology within an ∞-topos*.

**HTT** (Higher Topos Theory, Lurie 2009): a 925-page book. The ∞-categorical integration of this field + topology + algebra.

→ The deep motivation of HoTT (Univalent Foundations), the ∞-topos, and motivic.

## Why it matters

- **The most central of the serious mathematical domains**: number theory, algebra, geometry, analysis, categories, and ∞-categories are *all used simultaneously*. This field is *the serious quantum-gravity domain of mathematics* (the PHYSICS analogy).
- **The foundation of Wiles's FLT 1995**: Galois representations, modular forms, elliptic curves, and the Hecke algebra are all objects of this field. A direct successor to Number Theory §Act 3.
- **Grothendieck's vision (motives, étale, topos, schemes)**: even after 50 years its *complete realization is in progress*. Voevodsky, Lurie, and Scholze are the 21st-century form.
- **A PHYSICS bridge**: Calabi-Yau manifolds (String Theory), mirror symmetry (Strominger-Yau-Zaslow, Kontsevich), and gauge theory (Donaldson, Witten, Seiberg-Witten) are direct applications of this field.
- **An AI/CS bridge**: Lean formal proof, perfectoid, and the Liquid tensor experiment (Computability §Act 4) are the *formal verification* of this field. AI for math (DeepMind AlphaProof) proceeds in this field.
- **Hodge, BSD, Geometric Langlands**: Millennium + the open problems of this field form *the hardest cluster of open problems in mathematics*. The progress of this field = the future of mathematics.

## Historical context — R-03 four acts (mathematics variant)

### Act 1 — Birth: the Italian school, Hilbert, Zariski (1880–1937)

- **1880s–1900s**: **Cremona, Castelnuovo, Enriques, Severi**, the **Italian school** — the development of the intuition of surface classification, Riemann-Roch, and intersection theory. Rich in geometric insight but with *no formal foundation*
- **1893**: **Hilbert**, the **Nullstellensatz** — the exact bridge polynomial ideals ↔ varieties
- **1900**: **Hilbert** 23 problems — No. 14 *the finite generation of invariant rings*, No. 15 *the rigorization of Schubert calculus*
- **1900–1930**: **Schubert calculus** — the enumerative geometry of the Grassmannian
- **1930s**: **Bartel L. van der Waerden**, *Moderne Algebra* + attempts to make algebraic geometry precise
- **1937**: **Zariski**, the start of *abstract algebraic geometry* — refining the Italian intuition via *ring theory*
- **1946**: **Weil**, the book *Foundations of Algebraic Geometry* — the first formalization using the universal domain

→ Italian-school intuition → the start of Hilbert's formalism → the *formalization attempts* of Zariski and Weil. But *no fully satisfactory tool* yet.

**Decisive turn**: Hilbert 1893 + Zariski 1937 — the demand that *Italian geometric intuition* requires *ring-theoretic* tools was made explicit → the basis for Grothendieck's refoundation.

### Act 2 — Formalization: Serre, Grothendieck, EGA, SGA (1955–1973)

- **1955**: **Serre**, *Faisceaux Algébriques Cohérents* (FAC) — the introduction of sheaves into algebraic geometry. Fields 1954 (at 27, the youngest ever)
- **1956**: **Serre**, *GAGA* (Géométrie Algébrique et Géométrie Analytique) — the equivalence algebraic ↔ analytic
- **1957**: **Grothendieck**, the *Tôhoku paper* — general homological algebra, Abelian categories, sheaf cohomology
- **1957–1962**: **Grothendieck**, **EGA** I–IV (Éléments de géométrie algébrique) — the formalization of the *scheme*. Thousands of pages
- **1958**: **Grothendieck**, the ICM address — the official announcement of the scheme vision
- **1960–1973**: **Grothendieck (with Artin, Verdier, Demazure, Raynaud)**, **SGA** 1–7 (Séminaire de géométrie algébrique) — étale, crystalline, topos, motives. Tens of thousands of pages
- **1966**: **Grothendieck**, the Fields Medal — the vision of schemes, étale, motives
- **1971**: **Hartshorne**, the book *Algebraic Geometry* — the standard Grothendieck textbook (the modern learning entry point)
- **1973**: **Grothendieck**, resignation from the IHES — a sociology-of-academia meta-open-problem (the anonymous 10,000-page *Récoltes et Semailles* of the 1980s)

→ Grothendieck refounded algebraic geometry within *17 years*, from age 26 (1957) to 45 (1973). EGA + SGA amount to *tens of thousands of pages*. All the Italian intuition was refined into formal tools.

**Decisive turn**: Grothendieck 1957–1973 — the *Italian intuition* was refounded as *scheme + sheaf + topos + motive* → this field was transformed into a *tool for the integration of mathematics*. The possibility of Wiles 1995 was conceived.

### Act 3 — Verification/extension: Deligne, Faltings, Mori, Wiles (1973–1995)

- **1973–74**: **Deligne**, the **proof of the Weil conjectures** — using Grothendieck's étale (Fields 1978)
- **1976**: **Yau**, the **proof of the Calabi conjecture** — the existence of Calabi-Yau manifolds (Fields 1982). A direct string-theory application
- **1976–1980s**: **Mori**, the **minimal model program (the Mori program)** — the classification of 3-folds (Fields 1990)
- **1980s**: **Faltings**, the *proof of the Mordell conjecture* — the finiteness of rational points on curves of genus ≥ 2 (Number Theory §Act 3)
- **1985–95**: **Frey, Ribet, Wiles**, the **proof of Fermat's Last Theorem** — Galois representations + modular forms (a direct application of this field)
- **1980s–90s**: **Witten, Atiyah-Bott**, **TQFT, Donaldson, Seiberg-Witten** — gauge theory becomes a tool of this field (Geometry §Act 3)
- **1991**: **Kontsevich**, the *proof of the Witten conjecture* — the intersection numbers of the moduli space of curves (Fields 1998)
- **1994**: **Kontsevich**, the *Homological Mirror Symmetry* conjecture — the *algebraic ↔ geometric* symmetry between Calabi-Yaus

→ *Dozens of open problems solved* with Grothendieck's tools. The Weil conjectures, Mordell, FLT, the Witten conjecture, and mirror symmetry are all direct to this field.

**Decisive turn**: Wiles 1995 — the completion of the flow *Italian intuition → Grothendieck formalism → the solution of a 350-year-old open problem* → this field becomes the proving ground of the *extreme effect of abstract tools*.

### Act 4 — Reinterpretation/application: derived AG, motivic, perfectoid, ∞-topos (1995–present)

- **2002**: **Voevodsky**, the formalization of **motivic cohomology** + the generalization of the Bloch-Kato conjecture (Fields 2002)
- **2005**: **Toën-Vezzosi**, **Homotopical Algebraic Geometry I·II** — the formalization of derived algebraic geometry
- **2009**: **Lurie**, **Higher Topos Theory** (HTT) — the ∞-topos in earnest (925 pp.)
- **2009**: **Lurie**, **Higher Algebra** (HA) — ∞-categorical algebra
- **2012**: **Scholze**, the **perfectoid space** — a new tool for p-adic Hodge and local Langlands (Fields 2018, at 30)
- **2013–**: **Voevodsky**, *Univalent Foundations* (HoTT) — this field + foundations (Foundations of Mathematics §Act 4)
- **2014**: **Bhargava**, the *geometry of numbers* + the average Mordell-Weil rank (Fields 2014)
- **2017–**: **Bhatt-Morrow-Scholze**, **prismatic cohomology** — the integration of p-adic Hodge
- **2018**: **Scholze + Clausen**, *condensed mathematics* — the integration of topology + algebra
- **2020**: the **Liquid tensor experiment** (Scholze + Lean 4) — the formal verification of a core theorem of *condensed math*
- **2021**: **Fargues-Scholze**, **Geometric Local Langlands** — using the perfectoid
- **2023–**: **Mathlib, Lean 4 + AI** — the *computer verification* of theorems of this field in progress
- **Present**: the **complete proof of Geometric Langlands** — Beilinson, Gaitsgory 2024 partial answers, Frenkel et al.

→ Voevodsky's motivic + Lurie's ∞-topos + Scholze's perfectoid are the *21st-century form of this field*. Lean formal proof + AI in progress.

**Decisive turn**: Lurie's ∞-topos + Scholze's perfectoid + the Liquid tensor experiment — *∞-categories + p-adic + computer verification* proceeding simultaneously → this field is *the most future-facing dimension of mathematics* + *the all-field integrating tool* + *formal verification*.

### Core insight

The four acts of this field are the roughly 130-year flow *Italian geometric intuition → Grothendieck schemes → motivic, perfectoid, ∞-topos*. The first serious domain in which *all the tools of Foundations 1, 2, 3, and 4 act simultaneously*. A position *equal to* the 9 serious quantum-gravity items of PHYSICS — the first case of *serious-domain consolidation* in mathematics.

The meta-mechanism of this field: **AG = the stage of the simultaneous operation of Foundations 1, 2, 3, and 4**. Foundations of Mathematics (foundations, HoTT) + Analysis (sheaves, Hodge, spectral) + Algebra (Galois, Noether, categories) + Geometry (manifolds, homotopy, étale, de Rham) + Number Theory (Wiles, Langlands, perfectoid) + Probability (motivic + free probability not yet connected) + Computability (Lean, AI formal proof) — **all 7 fields feed directly into this field**.

The PHYSICS return: String Theory Calabi-Yau, mirror symmetry + AdS/CFT AdS holography + Operator algebra in Quantum Gravity — Type III and modular flow Type III + Swampland Cobordism — *4 PHYSICS Modern notes directly return*.

## Self-identified open problems (U-14)

Self-identified by this note:

1. **The Hodge conjecture** — Millennium. Algebraic cycle = Hodge class (an exact match with Geometry U-14 §1 and Algebra U-14 §4)
2. **The Tate conjecture** — the *p-adic dimension* of Hodge — Galois representation ↔ algebraic cycle
3. **The Standard conjectures** (Grothendieck) — the consistency of the *cohomological* representation of algebraic cycles
4. **A complete proof of Geometric Langlands** — Fargues-Scholze 2021 a partial answer
5. **The general Hodge–Tate decomposition** — Bhatt-Morrow-Scholze prismatic a partial answer, the *complete generalization of p-adic Hodge* unfinished
6. **An exact proof of mirror symmetry** — Kontsevich's homological MS 1994 a partial answer, a complete proof unfinished
7. **Mochizuki's ABC (IUT)** — a sociology-of-academia meta-open-problem (consistent with Number Theory U-14 §4, §10)
8. **The *classical meaning* of derived AG** — *which classical theorems DAG newly proves* is a meta-open-problem
9. **The *foundational status* of the ∞-topos** — the exact equivalence of the three: ZFC, HoTT, ∞-topos (an exact match with Foundations of Mathematics U-14 §3, §6)
10. **The generalization of AG's *physical* applications** — whether Calabi-Yau and mirror symmetry apply to *every quantum-gravity model* (the depth of Swampland)

## Connections

- **All Foundations feed in directly**: Foundations of Mathematics + Analysis + Algebra + Geometry + Number Theory + Probability + Computability — all 7 are direct to this field
- PHYSICS direct → String Theory: Calabi-Yau manifolds, mirror symmetry
- PHYSICS direct → AdS/CFT: holography geometric
- PHYSICS depth → Operator algebra in Quantum Gravity — Type III and modular flow: Type III + free probability
- PHYSICS depth → Swampland: Swampland, Cobordism, Calabi-Yau moduli
- Adjacent serious domains (branching candidates) → Langlands, ∞-categories (Lurie), non-commutative geometry (Connes), serious mirror symmetry
- Meta-position → Learning Philosophy §2-2 A *mathematics = a correct answer exists* + the most serious instance of the *proving ground of field integration*

## Workflow Connections
- index
- Foundations of Mathematics
- Analysis
- Algebra
- Geometry
- Number Theory
- Probability
- Computability
- String Theory
- Operator algebra in Quantum Gravity — Type III and modular flow
- User Model
