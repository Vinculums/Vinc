# Lie Groups ↔ Gauge Theory

## One-line essence
The mathematical foundation of the Standard Model itself, established stepwise in Yang-Mills 1954·Noether 1918·Atiyah 1970s: that the *representations of a Lie group* and the *connection of a principal bundle* are exactly the same thing as the *fundamental particles and gauge interactions of physics*.

## Mathematics side

Algebra §Acts 3·4:
- **Lie group**: a smooth manifold + group structure
- **Lie algebra** $\mathfrak{g}$: tangent space at identity
- **Representation theory** $\rho : G \to GL(V)$
- **Principal $G$-bundle** $P \to M$: a $G$-action over a manifold
- **Connection** $A \in \Omega^1(P, \mathfrak{g})$
- **Curvature** $F = dA + A \wedge A \in \Omega^2(P, \mathfrak{g})$
- **Holonomy** (Wilson loop): $\text{Hol}(\gamma) = P \exp \oint A$
- **Characteristic class** (Chern·Pontryagin·Euler): topological invariant

## Physics side

Core of Quantum Field Theory §Acts 2·3:
- **Gauge symmetry**: invariant under local $G$ transformations
- **Yang-Mills action**: $S = -\frac{1}{4} \int \text{Tr}(F_{\mu\nu} F^{\mu\nu}) d^4x$
- **Standard Model gauge group**: $SU(3)_C \times SU(2)_L \times U(1)_Y$
- **Higgs mechanism**: $SU(2) \times U(1) \to U(1)_{EM}$ spontaneous breaking
- **Wilson loop**: color confinement
- **Anomaly**: quantum gauge breaking (axial anomaly·Adler-Bell-Jackiw)
- **Instanton**: BPST 1975·topological charge

## Exact equivalences·dictionary

| Mathematics | ⟺ | Physics |
|---|---|---|
| Lie group $G$ | ⟺ | gauge symmetry group |
| Lie algebra $\mathfrak{g}$ | ⟺ | infinitesimal gauge transformation |
| unitary representation $\rho$ of $G$ | ⟺ | particle type (electron·quark·neutrino) |
| irreducible representation | ⟺ | a single particle type |
| Cartan subalgebra·weight | ⟺ | quantum number (charge·color·spin) |
| principal $G$-bundle $P \to M$ | ⟺ | background spacetime of the gauge field |
| connection $A_\mu$ | ⟺ | gauge boson (photon·gluon·W·Z) |
| curvature $F_{\mu\nu}$ | ⟺ | field strength (electromagnetic field $E, B$) |
| Wilson loop $\text{Tr}\, P \exp \oint A$ | ⟺ | binding of a quark pair (color confinement) |
| $\text{Hol}(\gamma) = e$ ⟺ flat connection | ⟺ | vacuum state |
| Yang-Mills equation $\nabla^\mu F_{\mu\nu} = J_\nu$ | ⟺ | equation of motion of the gauge field |
| self-dual connection $F = \star F$ | ⟺ | instanton (BPST 1975) |
| second Chern number $c_2 \in \mathbb{Z}$ | ⟺ | topological charge (winding) |
| Atiyah-Singer index | ⟺ | number of chiral fermions (anomaly·index) |
| spontaneous symmetry breaking | ⟺ | $G \to H$, $G/H$ vacuum manifold |
| Goldstone boson | ⟺ | the broken generator of $G/H$ |
| Higgs field $\phi$ | ⟺ | mediator of the $G \to H$ breaking |
| Yoneda lemma (∞-categorical dimension) | ⟺ | locality·gauge field functorial |
| Cartan-Killing form | ⟺ | kinetic term metric |
| BRST cohomology | ⟺ | physical states (gauge X equivalence) |
| anomaly polynomial $\text{ch}(F)$ | ⟺ | global anomaly·Green-Schwarz mechanism |

**Key theorems**:
- **Noether 1918**: continuous symmetry ↔ conserved quantity (1:1)
- **Peter-Weyl 1927**: compact Lie group → unitary dual = irrep
- **Cartan classification**: simple Lie algebras = $A_n, B_n, C_n, D_n, E_6, E_7, E_8, F_4, G_2$ (Dynkin diagram)
- **Yang-Mills 1954**: non-abelian gauge field equations
- **'t Hooft 1971·1972**: spontaneously broken non-abelian gauge theory is renormalizable
- **Atiyah-Singer 1963**: $\text{ind}(D) = \int_M \text{ch}(\sigma) \cdot \text{td}(M)$
- **BPST 1975**: explicit $SU(2)$ instanton solution
- **Adler-Bell-Jackiw 1969**: axial anomaly (chiral symmetry quantum breaking)
- **'t Hooft anomaly matching**: low-energy ↔ high-energy anomaly agreement

## Open-problem matrix

Open problems of this linkage domain:

1. **Yang-Mills mass gap** (Millennium) — proving a mass gap > 0 for quantum Yang-Mills theory. The mathematical consistency is incomplete
2. **The mathematical mechanism of confinement** — the *exact mathematical definition*·proof of color confinement is incomplete (lattice a partial answer)
3. **General anomaly cancellation** — *why* the Standard Model's anomaly agreement is so fine-tuned is incomplete
4. **The *mathematical* necessity of the Standard Model gauge group $SU(3) \times SU(2) \times U(1)$** — why this group, GUT candidates (Georgi-Glashow $SU(5)$·$SO(10)$·$E_6$·$E_7$·$E_8$)
5. **The existence of the magnetic monopole** — Dirac 1931 conjecture, 't Hooft-Polyakov 1974 GUT prediction, experimentally unobserved
6. **Mathematical refinement of the instanton** — the *complete classification* of the moduli space (Donaldson-Witten) is incomplete
7. **The nature of Holonomy ↔ Wilson line** — *how far* the two representations are equivalent is incomplete
8. **The mathematical naturalness of the Higgs mechanism** — the *mathematical* derivation of the Higgs mass 125 GeV is incomplete
9. **The *completeness of the physical application* of the Lie group classification** — in *which physics the exotic group $E_8$ appears* is incomplete
10. **Geometric Langlands ↔ Yang-Mills** (Witten-Kapustin 2007 — N=4 SYM ⟺ Geometric Langlands) — Witten-Kapustin 2007 a partial answer, the *complete equivalence* is incomplete

## Connections·follow-ups

- **Previous linkage** → Differential Geometry ↔ General Relativity
- **Next linkage** → Topology ↔ Condensed Matter
- **Deepest linkage** → Witten-Kapustin 2007 — N=4 SYM ⟺ Geometric Langlands (the full-fledged dimension of this linkage)
- **Mathematics depth** → Algebra·Langlands program
- **Physics depth** → Quantum Field Theory·Operator algebra in Quantum Gravity — Type III and modular flow
- **Meta-position** → this linkage is the *mathematical foundation of the Standard Model itself*. Noether 1918 + Yang-Mills 1954 + Atiyah-Singer 1963

## Workflow Connections
- README
- index
- Differential Geometry ↔ General Relativity
- Witten-Kapustin 2007 — N=4 SYM ⟺ Geometric Langlands
- Algebra
- Quantum Field Theory
