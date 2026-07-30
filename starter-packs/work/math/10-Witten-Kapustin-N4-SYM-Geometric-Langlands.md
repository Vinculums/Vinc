# Witten-Kapustin 2007 — N=4 SYM ⟺ Geometric Langlands

## One-line essence
The **deepest single equivalence of the mathematics-physics linkage domain itself**, proved in a 200+ page paper in 2007: that the *S-duality (electric-magnetic duality) of 4-dimensional N=4 supersymmetric Yang-Mills* is *exactly the same mathematical structure* as the *functoriality (Langlands duality) of Geometric Langlands*.

## Mathematics side

Core of Geometric Langlands + Langlands program:
- **Formal statement of Geometric Langlands** (Beilinson-Drinfeld 2003):
$$D\text{-mod}(\text{Bun}_G(C)) \xleftrightarrow{\text{equiv}} \text{IndCoh}(\text{LocSys}_{\check{G}}(C))$$
- **$\check{G}$**: Langlands dual group ($G = SU(N)$ → $\check{G} = SU(N)/\mathbb{Z}_N$ = $PSU(N)$)
- **Hecke eigensheaf**: Galois representation ↔ automorphic eigensheaf
- **Wilson loop ↔ 't Hooft loop**: the two line operators

## Physics side

Core of Quantum Field Theory §Act 3·Operator algebra in Quantum Gravity — Type III and modular flow:
- **4D N=4 SYM**: the most symmetric gauge theory (maximally superconformal)
- **Coupling constant** $\tau = \frac{\theta}{2\pi} + \frac{4\pi i}{g^2}$ — complexified
- **S-duality** (Montonen-Olive 1977·Sen 1994·Vafa-Witten 1994):
$$Z[G, \tau] = Z[\check{G}, -1/\tau]$$
→ strong coupling ↔ weak coupling, $G \leftrightarrow \check{G}$
- **Wilson loop**: $\text{Tr}\, P \exp \oint A$ (electric)
- **'t Hooft loop**: magnetic dual (Dirac monopole)
- **Topological twist** (Vafa-Witten 1994·Geometric Langlands twist): N=4 → 2D sigma model

## Exact equivalences·dictionary

This is the *most precise dictionary in the linkage domain*. Direct reference to Kapustin-Witten 2007 §3·§5·§11:

| Mathematics (Geometric Langlands) | ⟺ | Physics (4D N=4 SYM) |
|---|---|---|
| curve $C$ (algebraic·complex) | ⟺ | spacetime factor $\Sigma_4 = \Sigma_2 \times C$ ($\Sigma_2$ = 2D Riemann surface) |
| gauge group $G$ | ⟺ | N=4 SYM gauge group |
| Langlands dual $\check{G}$ | ⟺ | S-dual group |
| $\text{Bun}_G(C)$ — $G$-bundle moduli | ⟺ | A-twist target space (symplectic) |
| $\text{LocSys}_{\check{G}}(C)$ — flat connection moduli | ⟺ | B-twist target space (complex) |
| GL twist (Kapustin-Witten) $t \in \mathbb{CP}^1$ | ⟺ | $t$-parameter family of topological twists |
| $t = i$ (A-model) | ⟺ | A-twist (Floer·Fukaya) |
| $t = 1$ (B-model) | ⟺ | B-twist (derived category) |
| D-module on $\text{Bun}_G$ | ⟺ | A-twist boundary condition |
| IndCoh on $\text{LocSys}_{\check{G}}$ | ⟺ | B-twist boundary condition |
| Geometric Langlands equivalence | ⟺ | **S-duality**: $\tau \to -1/\tau$ + $G \to \check{G}$ |
| Hecke functor $H_x$ | ⟺ | **'t Hooft loop** at $x \in C$ |
| Wilson loop $W_R(\gamma)$, $R$ rep of $G$ | ⟺ | electric line operator (directly) |
| eigensheaf condition $H_x \mathcal{F} = \sigma_x \otimes \mathcal{F}$ | ⟺ | **simultaneous 't Hooft-Wilson eigenstate** (S-duality preserved) |
| functoriality $\check{G}_1 \to \check{G}_2$ | ⟺ | gauge group breaking + duality cascade |
| geometric Satake equivalence | ⟺ | Wilson loop OPE algebra |
| categorical structure | ⟺ | extended TQFT (Lurie 2009 cobordism) |
| factorization algebra | ⟺ | chiral algebra·vertex operator algebra (Beilinson-Drinfeld 2003) |
| tame ramification at $x \in C$ | ⟺ | surface operator at $\{x\} \times \Sigma_2$ |
| wild ramification | ⟺ | irregular surface operator (Frenkel-Gaitsgory) |
| local Langlands (Fargues-Scholze 2021) | ⟺ | p-adic analog of N=4 SYM (in progress) |

**Key theorems** (Kapustin-Witten 2007):

**Theorem (Theorem A)**: 4D N=4 SYM on $\Sigma_2 \times C$ with GL twist at $t = i$ reduces to the 2D A-model with target $\text{Hitchin}_G(C)$.

**Theorem (Theorem B)**: the same theory at $t = 1$ reduces to the 2D B-model with target $\text{Hitchin}_{\check{G}}(C)$.

**Core identity**: S-duality $t \to 1/t$, $G \to \check{G}$ transforms, via the *Hitchin map*, into the *Geometric Langlands equivalence*.

→ **A mathematical conjecture is a direct consequence of a physical duality**. It is made explicit that the *Langlands letter of 60 years earlier* arises naturally from the *non-abelian generalization of the Maxwell electric-magnetic duality of 80 years earlier*.

## Open-problem matrix

Open problems of this linkage domain — *neither mathematics nor physics can answer them alone*:

1. **The *mathematical definition* of N=4 SYM** — a *fully consistent definition* of N=4 SYM via axiomatic QFT (Wightman·Haag-Kastler) is incomplete (including the Yang-Mills mass gap Millennium problem)
2. **Mathematical refinement of S-duality** — the *complete proof* of the Montonen-Olive 1977 conjecture is incomplete — strong evidence for N=4 SYM, a partial answer for general $\mathcal{N}=2$ SYM
3. **Complete proof of Geometric Langlands** — the Drinfeld-Gaitsgory 2023 announcement, academic verification in progress (a 300+ page paper series)
4. **Wild ramification geometric Langlands** — Frenkel-Gaitsgory a partial answer for the tame case, the wild generalization is incomplete
5. **Unification of Arithmetic ↔ Geometric Langlands** — Fargues-Scholze 2021 a partial answer for p-adic geometric local, the *complete unification* is incomplete
6. **Higher-dimensional Langlands** — curve $C$ dimension → higher-dimensional varieties unresolved (Frenkel-Yun 2023 in progress)
7. **Holographic Langlands** — Costello-Witten 2018·follow-ups, the formal unification of AdS/CFT + Langlands is incomplete
8. **Extended TQFT ↔ Geometric Langlands** — the *complete consistency* of Lurie 2009 cobordism + this linkage is incomplete
9. **Mirror symmetry ↔ S-duality** — the *common mechanism* of Kontsevich HMS + Witten-Kapustin S-duality is incomplete (the Geometric Langlands ↔ Homological Mirror Symmetry bridge)
10. **N=4 SYM ↔ AdS/CFT** — whether N=4 SYM ↔ Type IIB on $AdS_5 \times S^5$ (Maldacena 1997) can generalize this linkage to the *bulk reconstruction* dimension (the Bulk reconstruction bridge)

## Connections·follow-ups

- **Preceding linkages** → Functional Analysis ↔ Quantum Mechanics + Differential Geometry ↔ General Relativity + Lie Groups ↔ Gauge Theory + Topology ↔ Condensed Matter — all *partial tools* of this linkage
- **Mathematics depth** → Langlands program·Geometric Langlands·infinity categories·Algebraic geometry
- **Physics depth** → Quantum Field Theory·Operator algebra in Quantum Gravity — Type III and modular flow·AdS/CFT·String Theory·Bulk reconstruction
- **Follow-up linkages (P1)** → S-duality ↔ Langlands (the *historical generalization* of this linkage) + Mirror Symmetry ↔ Calabi-Yau (the Mirror symmetry ↔ S-duality bridge) + Derived Geometry ↔ TQFT (Lurie cobordism + this linkage)
- **Meta-position** → **this linkage = the deepest single equivalence of the mathematics-physics linkage domain itself**. It proves that the *meta-program of mathematical unification (Langlands)* and the *meta-duality of physical unification (S-duality)* are *different expressions of the same thing*. One of the most actively developing fields of 21st-century mathematics and physics

## Workflow Connections
- README
- index
- Functional Analysis ↔ Quantum Mechanics
- Differential Geometry ↔ General Relativity
- Lie Groups ↔ Gauge Theory
- Topology ↔ Condensed Matter
- Geometric Langlands
- Quantum Field Theory
- Operator algebra in Quantum Gravity — Type III and modular flow
