# infinity categories

## One-line essence
As the infinite-dimensional category in which *equivalences are morphisms and morphisms are objects* becomes a *foundation candidate to replace set theory*, the deepest 21st-century foundational integration of mathematics, physics, and CS proceeds.

## Intuitive picture

∞-categories and HoTT are *the 21st-century candidate for the foundation of mathematics*. If ZFC was the foundation for 100 years, this field is the *alternative foundation*.

Three scenes:

- **Quillen 1967 → Joyal, Lurie 2009**: about 40 years after Daniel Quillen abstracted homotopy theory via the *model category* — André Joyal gave the *quasi-category* (one model of the ∞-category), and Jacob Lurie formalized ∞-categories in earnest with the 925-page *Higher Topos Theory*. An ∞-category is infinite-dimensional: *objects, 1-morphisms, 2-morphisms, …, ∞-morphisms*.
- **Voevodsky 2006 — the UF vision**: Vladimir Voevodsky proposed *Univalent Foundations* — the *Univalence axiom*, that *equivalence is identity*. *Type = space, equality = homotopy*. A *foundation of mathematics* different from ZFC.
- **The Liquid tensor experiment 2020**: Peter Scholze requested the formal verification in Lean 4 of a *complex unverified theorem* (the core of condensed math). Completed in 18 months — the first serious case of the *AI-for-math era*. The foundation of **mathematics + formal proof + AI**.

The meta-insight of this field: **∞-categories and HoTT = the strongest 21st-century candidate for the foundation of mathematics + the integration of homotopy theory + formal proof**. The *currently ongoing* dimension of Foundations of Mathematics §Act 4 the pluralization of foundations.

## Formal structure

### The ∞-category (quasi-category)

**Joyal 1990s, Lurie 2009 formalization**: a simplicial set $X$ is a *quasi-category* ⟺ the inner horn extension property.

**Structure**:
- *Objects* (vertices)
- *1-morphisms* (edges)
- *2-morphisms* (2-simplices, the result of composition = up to homotopy)
- *…, ∞-morphisms*

**Key**: composition holds not up to *strict equality* but up to *homotopy-level* equivalence. *Higher coherence* is handled automatically.

### The model category (Quillen 1967)

**Model category**: a category + 3 classes (cofibrations, fibrations, weak equivalences) + 5 axioms.

**Examples**:
- $\text{Top}$ (Quillen) — Serre fibrations
- $\text{sSet}$ (Quillen, Joyal) — Kan fibrations, the Joyal model
- $\text{Ch}$ — chain complexes

→ *Homotopy theory = the homotopy category of a model category*. The *classical 1-categorical presentation* of the ∞-category.

### Higher Topos Theory (Lurie 2009, 925 pp.)

**∞-topos**: the ∞-categorical generalization of the 1-topos.

**Objects**: $\infty$-presheaves on $\mathcal{C}$ (an ∞-category).

**Lurie's theorems**:
- The ∞-Yoneda lemma
- The equivalence (∞,1)-category ↔ the Joyal model
- (∞,1)-topos = sheaves at the ∞-categorical level
- The adjoint functor theorem at the ∞-categorical level

→ The ∞-topos is the *unified stage* of all homotopy theory, geometry, and algebra.

### Univalent Foundations, HoTT (Voevodsky 2006–)

**Type theory** (Martin-Löf 1972): proposition = type, proof = inhabitant.

**Univalence Axiom** (Voevodsky 2006): $(A = B) \simeq (A \simeq B)$ — *equivalent types are identical*.

**HoTT** (Homotopy Type Theory): type theory + homotopy intuition.
- type = space
- term = point
- equality = path
- proof = homotopy

**Key applications**:
- *A foundation of mathematics* — a ZFC alternative
- *Formal proof* — Coq, Agda, Lean 4
- *Automatic homotopy* — Univalence handles equivalences

### Synthetic vs. analytic homotopy theory

**Analytic** (Quillen, Lurie): explicit constructions with model categories, simplicial sets, Kan complexes.

**Synthetic** (HoTT, Voevodsky): *direct* reasoning within type theory.

→ HoTT is *axiomatic homotopy theory* — with the single axiom of Univalence.

### ∞-stacks, ∞-sheaves, ∞-presheaves

**∞-presheaf** $\mathcal{F} : \mathcal{C}^{op} \to \infty\text{-Cat}$: a functor.

**∞-sheaf**: the descent property (preserving limits over *Čech covers*).

**∞-stack**: the derived stack — Toën-Vezzosi 2005, Lurie HTT, DAG.

→ This field is the foundation of *derived algebraic geometry* + *motivic homotopy* + *p-adic Hodge*.

### The Liquid tensor experiment (Scholze + Lean 4, 2020)

**Setup**: a core theorem of the *condensed mathematics* of Peter Scholze + Dustin Clausen.

**Challenge** (Dec 2020, Scholze's blog): please formally verify in Lean 4 a theorem of *condensed math* (the liquid tensor structure).

**Result** (Jul 2022, Buzzard, Massot et al.): 18 months, 100+ collaborators, tens of thousands of lines of Lean code. Verification complete.

→ **The first serious case of the integration of AI for math + the foundation of mathematics + Lean formal proof.** A signal of the 21st-century era of mathematical *formal verification*.

### Spectral Algebraic Geometry (Lurie's SAG)

**Setup**: $E_\infty$-rings (∞-categorical commutative rings).

**Objects**: spectral schemes — the generalization of derived schemes.

**Applications**: chromatic homotopy theory, algebraic K-theory, motivic, string theory.

## Why it matters

- **The 21st-century candidate for the foundation of mathematics**: the possibility of *equal authority* with ZFC — the serious verification of UF is in progress. The *most active* dimension of Foundations of Mathematics §Act 4 the pluralization of foundations.
- **Automatic handling of homotopy**: equivalences handled by the single Univalence axiom — the *axiomatic* expression of classical homotopy theory (Quillen, Lurie).
- **Formal proof + AI**: Lean 4, Coq, Agda, and HoTT are the foundation of *AI for math*. The tools of AlphaProof, DeepSeek-Prover, and the Liquid tensor experiment.
- **The foundation of the serious mathematical domains**: Geometric Langlands, perfectoid (Scholze), motivic (Voevodsky), and DAG *all use the tools of this field*.
- **A PHYSICS bridge**: TQFT classification (Lurie 2009, *On the classification of topological field theories*) is the bridge to PHYSICS Quantum Information and non-Abelian anyon. The ∞-topos is the *functorial formalization of quantum field theory*.
- **A CS/AI bridge**: HoTT, Lean 4, and dependent type theory are *programming languages + mathematics*. The foundation of functional languages (Idris, Coq), Curry-Howard, and verified software.

## Historical context — R-03 four acts (mathematics variant)

### Act 1 — Birth: Eilenberg-Mac Lane, Quillen, Boardman-Vogt (1945–1990s)

- **1945**: **Eilenberg-Mac Lane**, *General theory of natural equivalences* — the first formalization of categories, functors, natural transformations (cited in Algebra §Act 2)
- **1958**: **Kan**, *simplicial sets + the Kan condition* — the simplicial model of homotopy theory
- **1967**: **Quillen**, *Homotopical Algebra* — the formalization of the **model category**
- **1972**: **Martin-Löf**, *intuitionistic type theory* — the formalization of type theory (a foundation input to HoTT)
- **1973**: **Boardman-Vogt**, *Homotopy invariant algebraic structures* — the first definition of an ∞-category (operads, simplicial)
- **1980s**: **Joyal**, the first formalization of the *quasi-category* (the Joyal model)
- **1991**: **Joyal**, *Quasi-categories vs simplicial categories* — model comparison
- **1990s**: **Hovey, Hirschhorn**, *Model Categories and Their Localization* — the serious refinement of models

→ Quillen's 1967 model category is the starting point of *the abstraction of homotopy theory*. Joyal's 1980s quasi-category is the first explicit ∞-category. Martin-Löf's type theory is the HoTT input.

**Decisive turn**: Quillen 1967 + Joyal 1980s — *homotopy theory* becomes *abstract model categories + ∞-categories* → the *21st-century form* of this field is imminent.

### Act 2 — Formalization: Voevodsky's UF, Lurie's HTT, SAG (2002–2009)

- **2002**: **Voevodsky**, **motivic cohomology** + the Bloch-Kato conjecture (Fields 2002, cited in Algebra §Act 4)
- **2005**: **Toën-Vezzosi**, *Homotopical Algebraic Geometry I·II* — the formalization of **derived algebraic geometry (DAG)**
- **2006–**: **Voevodsky**, the start of the *Univalent Foundations* vision
- **2009**: **Lurie**, **Higher Topos Theory** (HTT, 925 pp.) — the ∞-topos formalized in earnest. Big book 1 of ∞-categories
- **2009**: **Lurie**, *On the classification of topological field theories* — the bridge of this field + TQFT
- **2009**: **Lurie**, **Higher Algebra** (HA, 1500+ pp.) — ∞-categorical algebra. ∞-operads, E_n-algebras, factorization algebras
- **2009**: **Awodey-Warren**, *Homotopy theoretic models of identity types*
- **2010–**: the **Univalent Foundations Program** at the IAS (Voevodsky, Awodey, Coquand, Shulman, Bauer, …)
- **2013**: **The HoTT Book**, *Homotopy Type Theory: Univalent Foundations of Mathematics* — a 600+-page collaborative book

→ Voevodsky's UF + Lurie's HTT proceed *simultaneously* (2006–2013). The *modern form* of this field is settled.

**Decisive turn**: Lurie's HTT 2009 + the HoTT Book 2013 — *the formalization of ∞-categories + the foundation candidate* simultaneously → this field becomes the *21st-century foundation of mathematics + homotopy-theory integration* dimension.

### Act 3 — Verification/extension: the application explosion, motivic, perfectoid (2010–2020)

- **2010s**: **Lurie**, *Spectral Algebraic Geometry* (SAG) — derived schemes + chromatic homotopy
- **2010s**: **Voevodsky**, the start of *Coq + UF* formal verification
- **2012**: **Scholze**, the **perfectoid space** — using ∞-category tools (Number Theory §Act 4, Algebraic geometry §Act 4)
- **2014**: **Voevodsky**, the stabilization of the IAS UF program
- **2017**: **Bhatt-Morrow-Scholze**, *prismatic cohomology* — ∞-categories + p-adic Hodge
- **2017**: **the death of Voevodsky** (at 51) — a UF meta-open-problem (succession in progress)
- **2018**: **Scholze + Clausen**, *condensed mathematics* — the integration of ∞-categories + topology + algebra
- **2018**: **Scholze**, the Fields Medal — perfectoid + ∞-categories
- **2019**: **Riehl-Verity**, *Elements of ∞-Category Theory* — the abstraction of ∞-categories themselves
- **Dec 2020**: **Scholze**, the **Liquid tensor experiment challenge** blog post — a request to verify condensed math in Lean 4
- **2020s**: **Mathlib, Lean 4 in earnest** — the mathematical formal library

→ ∞-categories and HoTT are *the tools of the most active fields of mathematics*. Scholze's perfectoid, prismatic, and condensed math are direct to this field. Voevodsky's death in 2017 is a meta-open-problem.

**Decisive turn**: Scholze 2018 + the Liquid tensor experiment 2020 — *∞-categories advance into the hardest mathematics*, even to *formal verification* → this field integrates the 3 dimensions *foundation of mathematics + application + formal verification*.

### Act 4 — Reinterpretation/application: formal verification, AI for math, holography, the future foundation (2020–present)

- **Jul 2022**: **Buzzard, Massot, Commelin et al.**, the **completion of the Liquid tensor experiment** — 18 months, 100+ collaborators
- **2022–**: the explosion of **Mathlib 4** — the mathematical formal library, 1M+ lines
- **2023**: **AlphaProof** (DeepMind), the IMO silver medal — *AI formal proof*
- **2023–**: **DeepSeek-Prover, LLEMMA, MathPile** — LLMs + formal proof
- **2023**: **Drinfeld-Gaitsgory**, the complete proof of Geometric Langlands (Geometric Langlands §Act 4) — using ∞-category tools
- **2024**: **Mathlib + Lean 4 + AI**, attempts at *automating Polymath 8*
- **2024**: **Tao, Buzzard, Castillo, …**, the *AI Mathematician* discussion in earnest
- **2024**: **Yoshua Bengio, Tao**, *Algorithm Discovery via AI* — an application of this field
- **Present**: the **holographic ∞-topos** — attempts to combine AdS/CFT + the ∞-topos (AdS/CFT)
- **Present**: the **serious application of TQFT classification (Lurie 2009)** — non-Abelian anyon, Quantum Information
- **Present**: **the future of HoTT — successors to the UF book**, *cubical type theory*, *directed type theory*
- **Present**: **AI for HoTT** — LLMs attempting to prove HoTT theorems

→ The completion of the Liquid tensor experiment 2022 + AlphaProof 2023 are the signals of the *AI-for-math* era. ∞-categories and HoTT proceed simultaneously in the 3 dimensions *foundation of mathematics + AI + physical application*.

**Decisive turn**: the Liquid tensor experiment 2022 + AlphaProof 2023 — *the formal verification of complex mathematics* + *AI formal proof* simultaneously → this field is *the most future-facing dimension of 21st-century mathematics*.

### Core insight

The four acts of ∞-categories and HoTT are the roughly 80-year flow *the abstraction of homotopy theory → a foundation candidate for mathematics → the integration of formal verification + AI*. Quillen 1967 → Lurie's HTT 2009 → Voevodsky's UF 2013 → Scholze's perfectoid 2012 → the Liquid tensor experiment 2022 → AlphaProof 2023.

The meta-mechanism of this field: **∞-categories and HoTT = the 21st-century stage of the integration of the foundation of mathematics + homotopy theory + formal proof**. The *most active* dimension of Foundations of Mathematics §Act 4 *the pluralization of foundations*. Geometric Langlands and Algebraic geometry use the tools of this field. Bridges to PHYSICS non-Abelian anyon TQFT classification and AdS/CFT holographic ∞-topos. **A U-23 candidate reaching N=4** (big root → branching → A2 fork — strengthening the qualitative-enrollment recommendation).

## Self-identified open problems (U-14)

Self-identified by this note:

1. **The equivalence of HoTT vs. ZFC** — a decade+ after the 2013 UF book, a precise mapping of *which theorems are proved differently in the two foundations* is unfinished (consistent with Foundations of Mathematics U-14 §3)
2. **The foundational status of the ∞-topos** — whether the Lurie-Joyal ∞-category *is defined over set theory or is a foundation in itself* is a meta-open-problem (consistent with Algebraic geometry U-14 §9)
3. **The *physical* interpretation of the Univalence axiom** — the quantum-mechanical and QFT meaning of *equivalent types = identity* is unfinished
4. **Cubical type theory** — an efficient computational model of HoTT (Cohen-Coquand-Huber-Mörtberg 2015) — a partial answer to the *computational interpretation of Univalence*
5. **Directed type theory** — the formalization of asymmetric (1-categorical) HoTT is unfinished (Riehl-Shulman 2017 a partial answer)
6. **The proof of the *equivalence of all models* of ∞-categories** — quasi-categories, simplicial, complete Segal, n-fold, … — the *model equivalences* are only partially verified
7. **The limits of AI for HoTT** — whether an LLM can understand the *essence of Univalence* is a meta-open-problem
8. **The generalization of the Liquid tensor experiment** — the possibility of formally verifying *every complex mathematical theorem* is unfinished
9. **The *physical* generalization of TQFT classification (Lurie 2009)** — the generalization to *all dimensions + all dimension shifts* is unfinished
10. **The number-theoretic application of HoTT** — perfectoid and motivic are tools of this field, but the possibility of directly verifying the *Riemann hypothesis* and *BSD* is unfinished

## Connections

- **Prerequisite learning**: Foundations of Mathematics (the pluralization of foundations) + Algebra (category theory) + Geometry (homology, homotopy) + Computability (Lean formal proof) + Algebraic geometry (sheaves, perfectoid, DAG) + Langlands program + Geometric Langlands (using ∞-category tools)
- Adjacent serious domains (branching successors) → non-commutative geometry (the Connes spectral triple), mirror symmetry (Kontsevich HMS, SYZ)
- PHYSICS direct → non-Abelian anyon: TQFT classification, Lurie 2009
- PHYSICS direct → Quantum Information: quantum computation + ∞-categories + topological
- PHYSICS depth → AdS/CFT: the holographic ∞-topos
- PHYSICS depth → Operator algebra in Quantum Gravity — Type III and modular flow: the operator algebra ↔ ∞-category bridge
- Meta-position → Learning Philosophy §2-2 A *mathematics = the pluralization of foundations + formal verification*, the 21st-century form

## Workflow Connections
- index
- Foundations of Mathematics
- Computability
- Algebraic geometry
- Langlands program
- Geometric Langlands
- non-Abelian anyon
- User Model
