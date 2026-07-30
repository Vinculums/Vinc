# Path Integral ↔ Stochastic Analysis

## One-line essence
The foundational linkage of probability theory with quantum field theory·statistical mechanics, proved in Feynman-Kac 1949: that *integration over the Wiener measure* and the *Feynman path integral* are *exactly equivalent via the Wick rotation $t \to -i\tau$*.

## Mathematics side

Probability §Acts 2·3 + Analysis:
- **Wiener measure** $\mathbb{W}$ on $C([0,T], \mathbb{R}^n)$ — the distribution of Brownian motion
- **Itô integral** $\int_0^T f(s)\, dB_s$
- **Itô formula**: $df(B_t) = f'\, dB_t + \frac{1}{2} f''\, dt$
- **SDE** $dX = \mu\, dt + \sigma\, dB$
- **Feynman-Kac formula** (1949):
$$u(x, t) = \mathbb{E}_x\left[ f(B_t) e^{-\int_0^t V(B_s) ds} \right]$$
→ heat equation + potential = Wiener integral
- **Large deviation** (Cramér·Sanov·Freidlin-Wentzell·Donsker-Varadhan)
- **Malliavin calculus** (variation on Wiener space)
- **Regularity structures** (Hairer 2014) (Probability §Act 4)

## Physics side

Lagrangian Mechanics·Quantum Field Theory·Statistical Mechanics:
- **Feynman path integral (1948)**:
$$\langle x_f | e^{-iHT/\hbar} | x_i \rangle = \int \mathcal{D}x\, e^{iS[x]/\hbar}$$
- **Wick rotation** $t \to -i\tau$ → Euclidean path integral
- **QFT partition function**: $Z = \int \mathcal{D}\phi\, e^{-S_E[\phi]/\hbar}$
- **Renormalization**: handling divergences (Wilson-Polchinski·Kadanoff)
- **Lattice gauge theory** (Wilson 1974): lattice regularization
- **Statistical mechanics partition function**: $Z = \text{Tr}\, e^{-\beta H}$ — *exactly parallel to the path integral*

## Exact equivalences·dictionary

| Mathematics (probability) | ⟺ | Physics (quantum/statistical) |
|---|---|---|
| Wiener measure $\mathbb{W}$ | ⟺ | Euclidean path integral measure $e^{-S_E}$ |
| Brownian motion $B_t$ | ⟺ | trajectory of a Euclidean free quantum particle |
| Wick rotation $t \to -i\tau$ | ⟺ | Lorentzian → Euclidean transformation |
| Feynman-Kac formula | ⟺ | path-integral representation of the Schrödinger·heat equation |
| Itô formula | ⟺ | QM refinement·Stratonovich vs Itô ↔ ordering ambiguity |
| SDE $dX = \mu\, dt + \sigma\, dB$ | ⟺ | Langevin equation (Brownian motion + drift) |
| Fokker-Planck equation | ⟺ | time evolution of the density matrix (open quantum system) |
| Hairer regularity structures | ⟺ | QFT renormalization (Phi^4·KPZ·Yang-Mills) |
| large deviation rate function $I(x)$ | ⟺ | classical action $S[x]/\hbar$ (rare event = saddle point) |
| Freidlin-Wentzell theorem | ⟺ | semiclassical (WKB) limit |
| Donsker-Varadhan large deviation | ⟺ | thermodynamic limit + free energy |
| Wiener chaos expansion | ⟺ | Fock space (multi-particle state) |
| Malliavin calculus | ⟺ | QFT functional derivative |
| stochastic dominance | ⟺ | spectral gap·thermalization |
| Donsker invariance principle | ⟺ | universality (CLT·Lévy general) |
| Markov property | ⟺ | quantum locality·cluster decomposition |
| reflection positivity | ⟺ | Wightman axiom·Osterwalder-Schrader 1973 |
| Schramm-Loewner Evolution (SLE) | ⟺ | 2D critical phenomena·CFT (Smirnov 2010 Fields) |
| stochastic quantization (Parisi-Wu 1981) | ⟺ | candidate mathematical definition of QFT |
| KPZ equation (Hairer regularity) | ⟺ | random growth + 2D CFT |

**Key theorems**:
- **Wiener 1923**: a σ-additive measure on $C([0,T])$ (Brownian motion)
- **Feynman 1948**: introduction of the path integral
- **Kac 1949**: precise representation of the heat equation via the Wiener measure (Feynman-Kac formula)
- **Osterwalder-Schrader 1973**: Euclidean QFT axioms — equivalent to the Wightman axioms (reflection positivity)
- **Parisi-Wu 1981**: stochastic quantization — QFT = the long-time limit of a Langevin equation
- **Glimm-Jaffe 1981**: constructive QFT — mathematical consistency of Phi^4 in 2D·3D (4D unresolved)
- **Hairer 2014**: regularity structures — solving singular SPDEs (KPZ·Phi^4·Navier-Stokes)
- **Smirnov 2010 (Fields)**: SLE_κ ↔ 2D critical lattice model
- **Donsker 1952**: invariance principle — random walk → Brownian
- **Donsker-Varadhan 1975~83**: general large deviation

## Open-problem matrix

Open problems of this linkage domain:

1. **Constructive QFT in 4D Yang-Mills** — Glimm-Jaffe a partial answer in 2D·3D, 4D Millennium ⊆
2. **General consistency of stochastic quantization** — the Parisi-Wu 1981 formalism, the *consistent derivation of every QFT* is incomplete
3. **Consistency of the Wick rotation** (Osterwalder-Schrader axioms) — its application to 4D Yang-Mills is incomplete
4. **Complete parallel Hairer regularity ↔ QFT renormalization** — a partial answer, the *parallel for every QFT* is incomplete (Phi^4·KPZ verification)
5. **Complete SLE ↔ CFT dictionary** — Smirnov 2010 a partial answer, the generalization to *every CFT* is incomplete
6. **Navier-Stokes regularity** (Millennium) — SDE + Hairer a partial answer, completely unresolved
7. **Complete classification of KPZ universality** — Tracy-Widom a partial answer, *every 1+1 random growth* is incomplete
8. **Malliavin calculus ↔ QFT functional derivative** — a partial answer, the complete generalization to *gauge theory* is incomplete
9. **Complete equivalence Wiener chaos ↔ Fock space** — a partial answer for free fields, *interacting fields* incomplete
10. **Stochastic ↔ deterministic QFT** — the complete consistency of the *quantum back-reaction* of Wiener path → classical action is incomplete

## Connections·follow-ups

- **Mathematics depth** → Probability §Acts 2·3·4·Analysis
- **Physics depth** → Quantum Field Theory·Statistical Mechanics·Lagrangian Mechanics
- **Related linkage** → Functional Analysis ↔ Quantum Mechanics (Hilbert·QM foundation)
- **Related linkage** → Lie Groups ↔ Gauge Theory (Yang-Mills mass gap)
- **Related linkage** → Modular Forms ↔ String Theory (string partition function ↔ path integral)
- **Related linkage** → Witten-Kapustin 2007 — N=4 SYM ⟺ Geometric Langlands (N=4 SYM path integral)
- **Meta-position** → this linkage is the *foundational linkage of probability theory with QFT·statistical mechanics* — Feynman-Kac 1949 is the starting point, Hairer 2014 the 21st-century culmination

## Workflow Connections
- README
- index
- Functional Analysis ↔ Quantum Mechanics
- Lie Groups ↔ Gauge Theory
- Modular Forms ↔ String Theory
- Probability
- Quantum Field Theory
- Statistical Mechanics
