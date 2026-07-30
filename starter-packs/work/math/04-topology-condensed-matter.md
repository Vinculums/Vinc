# Topology ↔ Condensed Matter

## One-line essence
The decisive linkage of the 21st-century *mathematization* of condensed matter·quantum information, established stepwise in TKNN 1982·Kane-Mele 2005·Kitaev periodic table 2009: that the *topological invariants of a manifold* are *exactly the same thing* as the *quantized response coefficients of a topological phase of matter*.

## Mathematics side

Core of Geometry §Acts 3·4:
- **Chern class** $c_n \in H^{2n}(M, \mathbb{Z})$ — vector bundle topological invariant
- **K-theory** $K(M)$ — vector bundle Grothendieck group
- **Cohomology** $H^*(M, \mathbb{Z})$
- **Index theorem** (Atiyah-Singer 1963)
- **Bott periodicity**: $K(\mathbb{R}^n) \cong K(\mathbb{R}^{n+8})$ (real), $n+2$ (complex)
- **Twisted K-theory** (Freed-Hopkins-Teleman)
- **Cobordism** (Geometry §Act 3·Swampland)

infinity categories §Acts 3·4:
- **∞-categorical TQFT classification** (Lurie 2009)

## Physics side

Core of Condensed Matter·non-Abelian anyon:
- **Quantum Hall effect** (IQHE·FQHE): $\sigma_{xy} = n \frac{e^2}{h}$ quantization
- **Topological insulator** (TI): bulk gap·protected boundary state
- **Topological superconductor**: Majorana zero mode
- **Topological qubit**: Kitaev anyonic·non-abelian statistics·Fibonacci
- **Symmetry-protected topological phase** (SPT)
- **Anomaly inflow** (Callan-Harvey 1985·Witten 2016)

## Exact equivalences·dictionary

| Mathematics | ⟺ | Physics |
|---|---|---|
| first Chern number $c_1 \in \mathbb{Z}$ | ⟺ | IQHE Hall conductance $\sigma_{xy}/(e^2/h)$ |
| TKNN formula $c_1 = \frac{1}{2\pi i} \int_{BZ} F$ | ⟺ | Berry curvature integral ([Thouless-Kohmoto-Nightingale-den Nijs 1982]) |
| Berry connection $A = \langle u | i\partial_k | u \rangle$ | ⟺ | Bloch wave geometric phase |
| K-theory $KO^n$ / $KU^n$ | ⟺ | Kitaev periodic table 10-fold way (free fermion) |
| Bott periodicity | ⟺ | Altland-Zirnbauer 10 classes ↔ 8+2 periodic |
| index of Dirac operator | ⟺ | number of chiral zero modes |
| $\mathbb{Z}_2$ invariant (Kane-Mele) | ⟺ | $\mathbb{Z}_2$ topological insulator (QSHE) |
| cobordism class $\Omega_n^{Spin}$ | ⟺ | SPT phase classification (Kapustin-Thorngren-Turzillo-Wang 2014) |
| cohomology with coefficients $H^d(G, U(1))$ | ⟺ | bosonic SPT phase classification |
| TQFT functor $Z : \text{Bord}_n \to \text{Vect}$ | ⟺ | low-energy effective theory of a gapped phase |
| modular tensor category | ⟺ | anyon braiding·non-abelian statistics |
| Fibonacci category | ⟺ | Fibonacci anyon (universal quantum computation) |
| Ising category | ⟺ | Majorana zero mode |
| anomaly polynomial $I_{d+2}$ | ⟺ | anomaly inflow (bulk-boundary) |
| Atiyah-Patodi-Singer η-invariant | ⟺ | gravitational/parity anomaly |
| (∞,n)-category | ⟺ | extended TQFT (Lurie 2009 cobordism hypothesis) |
| higher group $BG$ | ⟺ | higher-form symmetry |
| sphere bundle Euler class | ⟺ | Euler-Mott·Hopf insulator |

**Key theorems**:
- **TKNN 1982**: IQHE Hall conductance = first Chern number (topological quantization)
- **Atiyah-Singer 1963**: index of Dirac operator = topological invariant
- **Kitaev periodic table 2009**: free fermion topological phase = $KO_n / KU_n$ (10-fold way)
- **Kane-Mele 2005**: $\mathbb{Z}_2$ topological insulator (QSHE)
- **Hasan-Kane 2010·Qi-Zhang 2011**: general classification of TI
- **Kitaev chain 2001**: 1D topological superconductor + Majorana zero mode
- **Freed-Hopkins-Teleman 2011**: twisted K-theory ↔ chiral RCFT
- **Lurie cobordism hypothesis 2009**: fully extended TQFT = framed bordism category functor
- **Witten 2016**: anomaly inflow + cobordism (modern consistency)
- **Kapustin-Thorngren-Turzillo-Wang 2014**: bosonic SPT = group cohomology

## Open-problem matrix

Open problems of this linkage domain:

1. **Complete classification of the fractional quantum Hall effect (FQHE)** — the *mathematical* complete theorem of Laughlin·Halperin·non-abelian·Pfaffian is incomplete
2. **Experimental verification of the topological qubit** — Microsoft Majorana 1 (2025) ↔ academic verification in progress
3. **The reality of non-abelian anyons** — the experimental verification of Pfaffian·Fibonacci·Ising is incomplete
4. **The *complete proof* of the Lurie cobordism hypothesis 2009** — the book remains unpublished after the Hopkins-Lurie announcement (consistent with infinity categories U-14 §6)
5. **Classification of interacting topological phases** — the free fermion → interacting generalization is incomplete
6. **Generalization of anomaly cancellation** — the *generalization to all dimensions* of Witten 2016's cobordism inflow is incomplete
7. **The *higher-dimensional* generalization of topological insulators** — the explicit classification 3D TI → 4D·5D TI is incomplete
8. **Quantum simulation ↔ topology** — realizing·verifying topological phases with analog quantum simulators (cold atom·photonic)
9. **The *physical degrees of freedom* of TQFT** — *which case* of the Lurie classification is real matter
10. **The topological qubit fault-tolerance threshold** — the limits of surface code·color code ↔ experimental reach (non-Abelian anyon directly)

## Connections·follow-ups

- **Previous linkage** → Lie Groups ↔ Gauge Theory
- **Next linkage** → Witten-Kapustin 2007 — N=4 SYM ⟺ Geometric Langlands (the deepest linkage)
- **Related linkage** → Quantum Computing ↔ Complexity (P1, topological qubit applications)
- **Related linkage** → Derived Geometry ↔ TQFT (P1, Lurie cobordism hypothesis)
- **Related linkage** → Operator Algebra ↔ Quantum Gravity (P1, Type III + topological algebra)
- **Mathematics depth** → Geometry·infinity categories
- **Physics depth** → Condensed Matter·non-Abelian anyon·Quantum Information
- **Meta-position** → this linkage is the *mathematization of 21st-century condensed matter*. TKNN 1982 → Lurie 2009 → the topological qubit industry

## Workflow Connections
- README
- index
- Lie Groups ↔ Gauge Theory
- Witten-Kapustin 2007 — N=4 SYM ⟺ Geometric Langlands
- Geometry
- Condensed Matter
- non-Abelian anyon
