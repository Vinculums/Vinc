# Differential Geometry ↔ General Relativity

## One-line essence
The most classical and deepest case of the mathematics-physics linkage, formalized in Einstein's 1915 *Die Feldgleichungen der Gravitation*: that the *curvature of a Riemannian manifold* is *exactly the same thing* as the *gravity of spacetime*.

## Mathematics side

Core tools of Geometry §Acts 1·2:
- **Manifold** $M$: locally homeomorphic to $\mathbb{R}^n$
- **Metric tensor** $g_{\mu\nu}$: a $(0,2)$-tensor field, symmetric·non-degenerate
- **Levi-Civita connection** $\nabla$: torsion-free·metric-compatible
- **Riemann curvature tensor** $R^\rho_{\sigma\mu\nu}$
- **Ricci tensor** $R_{\mu\nu} = R^\rho_{\mu\rho\nu}$
- **Scalar curvature** $R = g^{\mu\nu} R_{\mu\nu}$
- **Geodesic** $\ddot{x}^\mu + \Gamma^\mu_{\nu\rho} \dot{x}^\nu \dot{x}^\rho = 0$
- **Homology·cohomology** (Geometry §Act 3)

## Physics side

Core structures of General Relativity·Cosmology:
- **Equivalence principle**: free fall = weightlessness (Einstein 1907)
- **Einstein equations**: $G_{\mu\nu} + \Lambda g_{\mu\nu} = \frac{8\pi G}{c^4} T_{\mu\nu}$
- **Schwarzschild solution** (1916): spherically symmetric BH
- **FLRW cosmology**: isotropic·homogeneous
- **Hawking radiation** (Hawking radiation): $T_H = \frac{\hbar c^3}{8\pi G M k_B}$

## Exact equivalences·dictionary

| Mathematics | ⟺ | Physics |
|---|---|---|
| Lorentzian manifold $(M, g)$, $\text{sig}(g) = (-,+,+,+)$ | ⟺ | spacetime |
| metric tensor $g_{\mu\nu}$ | ⟺ | gravitational field |
| Levi-Civita connection $\nabla$ | ⟺ | parallel transport·inertial frame |
| Christoffel symbols $\Gamma^\mu_{\nu\rho}$ | ⟺ | gravitational force (coordinate-dependent) |
| geodesic equation | ⟺ | trajectory of a free-falling particle |
| Riemann curvature $R^\rho_{\sigma\mu\nu}$ | ⟺ | tidal force |
| Ricci tensor $R_{\mu\nu}$ | ⟺ | control of light-ray divergence (Raychaudhuri equation) |
| Einstein tensor $G_{\mu\nu} = R_{\mu\nu} - \frac{1}{2} R g_{\mu\nu}$ | ⟺ | coupling to the gravitational source |
| stress-energy tensor $T_{\mu\nu}$ | ⟺ | matter·energy·pressure |
| cosmological constant $\Lambda$ | ⟺ | vacuum energy·dark energy |
| Killing vector $\nabla_{(\mu} \xi_{\nu)} = 0$ | ⟺ | symmetry·conserved quantity (time·space) |
| light cone $g_{\mu\nu} dx^\mu dx^\nu = 0$ | ⟺ | causal structure |
| asymptotic flatness | ⟺ | Minkowski at infinity |
| Killing horizon | ⟺ | event horizon (BH surface) |
| Penrose-Carter diagram | ⟺ | visualization of the causal structure of spacetime |
| Hodge dual $\star F$ | ⟺ | electromagnetic dual·magnetic monopole |
| topology of $M$ | ⟺ | cosmic topology (Geroch 1967 topology change theorem) |
| conformal compactification | ⟺ | asymptotic infinity·BMS |

**Key theorems**:
- **Cartan structure equation**: $d\omega + \omega \wedge \omega = \Omega$ (curvature)
- **Bianchi identity**: $\nabla_{[\rho} R_{\mu\nu]\sigma\tau} = 0$ → $\nabla^\mu G_{\mu\nu} = 0$ (energy conservation automatic)
- **Birkhoff's theorem** (1923): spherically symmetric vacuum = Schwarzschild (static, unique)
- **Penrose-Hawking singularity theorem** (1965·1970): sufficiently strong energy → a spacetime singularity is inevitable
- **Positive Energy Theorem** (Schoen-Yau 1979·Witten 1981): asymptotic Minkowski + positive energy → ADM mass ≥ 0
- **Bartnik 1988**: a *geometric definition* of ADM mass
- **Geroch's no-go**: topology change produces closed timelike curves

## Open-problem matrix

Open problems of this linkage domain:

1. **Cosmic Censorship Hypothesis** (Penrose 1969) — the conjecture that naked singularities are absent. The mathematical proof is incomplete
2. **Generalization of the Positive Energy Theorem** — the *exact definition* of Bartnik mass·Hawking mass·quasi-local mass is incomplete
3. **The nature of singularities** — the Penrose-Hawking theorem proves only the *existence of a singularity*; its *geometric nature* is incomplete
4. **Lorentzian Calabi-Yau** — the Lorentzian generalization of Riemannian Calabi-Yau (Yau 1976) is incomplete
5. **Topology change** — is it allowed at the *quantum level* despite Geroch's 1967 prohibition? (Hawking·string-theory wormhole)
6. **The beginning·end of time** — the *mathematical definition* of the Big Bang·BH singularity is incomplete (vs Hartle-Hawking·no-boundary)
7. **Refinement of BH thermodynamics** — the *mathematical* derivation of Hawking radiation·Bekenstein-Hawking entropy is incomplete (CPW 2022 a partial answer)
8. **The mathematical consistency of AdS/CFT** — the exact one-to-one correspondence bulk gravity ↔ boundary CFT is incomplete (Maldacena 1997 conjecture)
9. **The *mathematical* identity of the inflaton field** — the unique refinement of the effective action of the cosmological inflaton is incomplete (inflaton directly)
10. **Topology and the BH information paradox** — how BH topology contributes to *information loss* is incomplete

## Connections·follow-ups

- **Previous linkage** → Functional Analysis ↔ Quantum Mechanics
- **Next linkage** → Lie Groups ↔ Gauge Theory
- **Related linkage** → Topology ↔ Condensed Matter (Chern·K-theory ↔ topological phase)
- **Related linkage** → Derived Geometry ↔ TQFT (P1, ∞-categorical dimension)
- **Mathematics depth** → Geometry·Algebraic geometry (Calabi-Yau)
- **Physics depth** → AdS/CFT·Hawking radiation·Operator algebra in Quantum Gravity — Type III and modular flow
- **Meta-position** → this linkage is the *first full-fledged case of the mathematics-physics linkage* — Einstein 1915 the first proof of *geometry = physics*

## Workflow Connections
- README
- index
- Functional Analysis ↔ Quantum Mechanics
- Geometry
- General Relativity
- Cosmology
- Hawking radiation
