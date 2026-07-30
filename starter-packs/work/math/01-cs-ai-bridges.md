# Math ↔ CS/AI

## One-line essence
The linkage — established stepwise over the 90 years from 1936 to the present — that the *formal limits of Gödel·Turing·Shannon* are exactly the same thing as the *real limits of algorithms·information·deep learning·LLMs*, applying directly to Dohun's Cloud PM·AI systems work.

## Mathematics side (6 inputs from the Math domain)

- Foundations of Mathematics §Acts 3·4 — Gödel·Cohen·HoTT·Lean
- Analysis §Acts 2·3·4 — linear·spectral·convex·variational
- Algebra §Acts 2·3·4 — category theory·Yoneda·representation
- Probability §Acts 2·3·4 — Kolmogorov·Bayes·MCMC·diffusion·causal
- Computability §Acts 1·2·3·4 — Turing·Cook-Levin·BQP·PCP·AlphaProof
- infinity categories §Acts 2·3·4 — Lurie·Voevodsky·Liquid tensor

## CS/AI side (Dohun's work — Cloud PM + AI systems)

- **Models of computation**: Turing machine·λ-calculus·register machine·quantum circuits
- **Algorithms·complexity**: P·NP·BPP·BQP·approximation·online·streaming·PCP·UGC
- **Information theory**: Shannon entropy·channel capacity·Kolmogorov complexity·Solomonoff induction
- **Machine learning**: PAC-learning (Valiant 1984)·VC dimension·Rademacher complexity·SGD·convex optimization
- **Deep learning**: backprop·CNN·RNN·Transformer (Vaswani 2017)·attention·diffusion·SSL·RLHF
- **LLM**: GPT-3·4·5·Claude·Gemini·Llama — scaling law·in-context learning·reasoning
- **AI for math**: AlphaProof 2024 IMO silver medal·Lean 4·DeepSeek-Prover·LLEMMA·Mathlib
- **Quantum computation**: Shor·Grover·quantum supremacy (Google Sycamore 2019·USTC Jiuzhang 2020)
- **Systems·infrastructure**: distributed systems (Paxos·Raft)·CAP·consensus·serverless·MCP·agent orchestration
- **Formal verification**: Lean·Coq·Agda·Isabelle·Curry-Howard·dependent types
- **Cryptography**: RSA·ECC·post-quantum (lattice·CSIDH·hash)·zk-SNARK·blockchain

## Exact equivalences·dictionary ⭐ (5+ axes unified)

### Axis 1 — Computability·complexity (Math 07 ↔ algorithms·CS)

| Mathematics | ⟺ | CS/AI |
|---|---|---|
| Turing machine | ⟺ | the model of all algorithms (Church-Turing) |
| undecidability of the halting problem | ⟺ | general impossibility of virus detection·program verification |
| Gödel incompleteness | ⟺ | Löb's theorem·limits of verified compilers·limits of self-reference |
| P vs NP (Millennium) | ⟺ | polynomial solvability of every NP-hard problem (SAT·TSP·scheduling·routing) |
| Cook-Levin SAT NP-complete | ⟺ | the foundation of the SAT solver industry (Z3·CVC5·MiniSat) |
| PCP theorem·UGC | ⟺ | the *limits* of approximation algorithms (MAX-CUT 0.878·VC 2) |
| Shor 1994 (BQP) | ⟺ | quantum-computer threat to RSA·ECC → post-quantum migration |
| Grover 1996 ($\sqrt{N}$) | ⟺ | the quantum speedup limit of search algorithms |
| Razborov-Rudich natural proofs | ⟺ | the meta-explanation of *why circuit lower bounds are hard* |

### Axis 2 — Information theory (Math 06·probability ↔ Shannon·Kolmogorov)

| Mathematics | ⟺ | CS/AI |
|---|---|---|
| Shannon entropy $H(X) = -\sum p \log p$ | ⟺ | compression limit·channel capacity·encoding |
| mutual information $I(X;Y)$ | ⟺ | feature selection·representation learning·infomax |
| Kullback-Leibler divergence | ⟺ | variational inference·diffusion model loss·RL policy gradient |
| Kolmogorov complexity $K(x)$ | ⟺ | Solomonoff induction·MDL·Occam's razor (undecidable, learnable approximation) |
| Wiener-Khinchin theorem | ⟺ | spectral analysis·time series·Fourier feature |
| rate-distortion theory | ⟺ | lossy compression (JPEG·MP3·neural codec) |

### Axis 3 — Linear algebra·calculus ↔ deep learning

| Mathematics | ⟺ | CS/AI |
|---|---|---|
| matrix multiplication $W \cdot x$ | ⟺ | fully-connected layer·affine transformation |
| tensor decomposition (CP·Tucker) | ⟺ | parameter-efficient fine-tuning (LoRA·QLoRA) |
| eigendecomposition·SVD | ⟺ | PCA·spectral clustering·the low-rank structure of attention |
| Jacobian·Hessian | ⟺ | backprop·second-order optimization (Newton·natural gradient·K-FAC) |
| automatic differentiation (Pearlmutter 1995) | ⟺ | PyTorch·JAX·TensorFlow autograd |
| chain rule $\nabla (f \circ g) = \nabla f \cdot \nabla g$ | ⟺ | the mathematical essence of backprop (Rumelhart-Hinton-Williams 1986) |
| convex optimization (Boyd-Vandenberghe) | ⟺ | convergence guarantees for SVM·logistic regression·SGD |
| convolution theorem $\mathcal{F}(f * g) = \mathcal{F}(f) \mathcal{F}(g)$ | ⟺ | CNN·spectral graph network·FFT acceleration |
| spectral graph theory (Cheeger inequality) | ⟺ | graph neural network·community detection·random walk |
| function approximation (Stone-Weierstrass·Cybenko 1989) | ⟺ | universal approximation theorem (MLP·neural network) |
| implicit function theorem | ⟺ | implicit layer·deep equilibrium model·neural ODE |

### Axis 4 — Probability·statistics ↔ ML

| Mathematics | ⟺ | CS/AI |
|---|---|---|
| Bayes' theorem $P(\theta|D) \propto P(D|\theta) P(\theta)$ | ⟺ | Bayesian inference·prior·posterior·GP·BNN |
| MCMC (Metropolis·HMC) | ⟺ | Stan·PyMC·Bayesian deep learning posterior sampling |
| variational inference (VI) | ⟺ | VAE·BNN·mean-field·ELBO maximization |
| score function $\nabla \log p$ | ⟺ | score-based generative model·diffusion (Song-Sohl-Dickstein) |
| stochastic differential equation | ⟺ | the mathematical foundation of diffusion models·flow matching·rectified flow |
| PAC learning (Valiant 1984) | ⟺ | generalization theory·sample complexity·VC dimension |
| Rademacher complexity | ⟺ | uniform convergence·generalization bound |
| large deviation·concentration (Hoeffding·McDiarmid) | ⟺ | confidence interval·learning theory·multi-arm bandit |
| martingale | ⟺ | online learning·exp3·regret minimization |
| causal inference (Pearl·Rubin) | ⟺ | A/B test·counterfactual·do-calculus·ITE estimation |
| random matrix theory (Marchenko-Pastur) | ⟺ | deep learning generalization·neural tangent kernel·loss landscape |

### Axis 5 — Category theory·HoTT ↔ type theory·functional languages

| Mathematics | ⟺ | CS/AI |
|---|---|---|
| Curry-Howard correspondence | ⟺ | proposition = type, proof = program (Haskell·OCaml·Lean) |
| Yoneda lemma | ⟺ | API design·duck typing·representable functor·React props |
| monad (Kleisli) | ⟺ | side-effect composition (IO·State·List·Maybe in Haskell·Scala·Rust) |
| functor·natural transformation | ⟺ | map·fmap·`functor` in category-theoretic libraries (cats·scalaz) |
| adjoint functor | ⟺ | parser/printer duality·optimization transformations·compiler IR |
| dependent type | ⟺ | Idris·Lean 4·Coq·Agda·verified software |
| Univalence Axiom (HoTT) | ⟺ | type isomorphism = equality (verified math + AI proof) |
| ∞-topos·∞-category (Lurie) | ⟺ | TQFT classification + verified math infrastructure (Mathlib) |
| initial algebra (Lambek) | ⟺ | recursive data type·fold·catamorphism |
| final coalgebra | ⟺ | infinite data·unfold·stream·anamorphism |
| operad | ⟺ | algebraic effect system·effect handler |
| linear logic (Girard) | ⟺ | Rust ownership·linear type·session type·resource management |

### Axis 6 — Optimization·convexity (Math 02 ↔ ML training)

| Mathematics | ⟺ | CS/AI |
|---|---|---|
| convex function | ⟺ | training guarantees for SVM·logistic·LASSO |
| Lagrangian duality | ⟺ | KKT conditions·SVM dual·LP dual |
| semidefinite programming (SDP) | ⟺ | combinatorial relaxation (MAX-CUT·Goemans-Williamson) |
| stochastic gradient descent | ⟺ | Robbins-Monro 1951·SGD standard·Adam·LAMB |
| mirror descent·proximal | ⟺ | online learning·natural gradient·proximal policy optimization (PPO) |
| mean field game (Lasry-Lions) | ⟺ | multi-agent RL·population-based training |
| optimal transport (Kantorovich·Wasserstein) | ⟺ | Wasserstein GAN·domain adaptation·Sinkhorn·trust region |

## Open-problem matrix (the linkage domain itself)

Open problems of this linkage domain — *irreducible to either Math or CS/AI alone*:

1. **P vs NP** (Millennium) — the most intuitive and hardest open problem of this linkage
2. **Exact separation of quantum vs classical computation** — the exact position of BQP vs BPP·P/poly
3. **Long-term stability of post-quantum cryptography** — whether lattice·hash·CSIDH can defend against *every quantum algorithm*
4. **A mathematical foundation of deep-learning generalization** — a consistent explanation of *why PAC·VC works for over-parametrized networks* is incomplete (double descent·neural tangent kernel partial answers)
5. **The *mathematical essence* of LLM scaling laws** — Chinchilla·Hoffmann 2022 is empirical; the theoretical foundation is incomplete
6. **The mechanism of in-context learning** — *why* GPT can learn from prompt examples (Bayesian·gradient descent in-context·induction heads)
7. **The *mathematical possibility* of AI alignment** — a consistent guarantee of the convergence + interpretability of RLHF·constitutional AI is incomplete
8. **The *genuine mathematical creativity* of AlphaProof + Lean** — the possibility of *discovering new theorems* (vs verifying existing ones)
9. **A *decidable approximation* of Solomonoff induction** — K-complexity is undecidable + the limits of learnable approximation
10. **Kolmogorov complexity ↔ ML inductive bias** — the exact quantification of Occam's razor is incomplete
11. **The *naturalness of the Yoneda lemma in API design*** — why abstract category theory is natural in *real software*
12. **HoTT vs ZFC equivalence ↔ the *real theorem difference* of formal verification** — the Liquid tensor experiment as a starting point
13. **Generalization of Curry-Howard** — the *complete mapping* dependent type ↔ general mathematical proof is incomplete
14. **The combination of quantum computers + AI** — the proof of the *superiority over classical* of quantum machine learning·variational quantum eigensolvers is incomplete
15. **The *complexity* limits of distributed systems·consensus** — refining the *real-system trade-offs* of Byzantine·CAP·FLP

## Historical context — R-03 four acts (mathematics variant)

### Act 1 — Birth: Turing·Shannon·McCulloch-Pitts·Solomonoff (1936~1960s)

- **1936**: Turing machine + Church-Turing thesis (Computability §Act 1)
- **1943**: McCulloch-Pitts neuron — the first mathematical model of a neural network
- **1948**: **Shannon**, *A Mathematical Theory of Communication* — information theory
- **1950**: Turing test
- **1951**: Robbins-Monro stochastic approximation — the foundation of SGD
- **1956**: Dartmouth conference — the academic launch of AI
- **1958**: Rosenblatt perceptron
- **1964**: **Solomonoff**, *universal induction*
- **1969**: Minsky-Papert *Perceptrons* — the first AI winter

→ Turing·Shannon laid the foundation of *computation + information*. McCulloch-Pitts·Rosenblatt began neural networks.

**Decisive turn**: Turing 1936 → Shannon 1948 — the *limits of computation* extended into the *limits of information* → the simultaneous launch of CS·AI.

### Act 2 — Formalization: Cook-Levin·PAC·backprop·CNN (1970~1995)

- **1971**: **Cook-Levin** SAT NP-complete
- **1972**: Karp's 21 NP-complete problems
- **1973**: the AI winter in earnest
- **1982**: **Hopfield network**·Boltzmann machine
- **1984**: **Valiant**, *PAC learning* — the mathematical foundation of generalization
- **1986**: **Rumelhart-Hinton-Williams**, **backpropagation** in earnest
- **1989**: **LeCun**, the first serious application of *CNN (LeNet)* (zip code recognition)
- **1990s**: SVM (Vapnik-Cortes 1995)·boosting (AdaBoost 1995)
- **1994**: **Shor 1994** — the RSA threat
- **1995**: Vapnik *Statistical Learning Theory*

→ Cook-Levin·Valiant provided *complexity·learning theory*. backprop·CNN laid the *foundation of deep learning*. Yet the *2nd AI winter* (1990s).

**Decisive turn**: backprop 1986 + CNN 1989 → *trainable neural networks* — but winter due to insufficient computing power.

### Act 3 — Verification·expansion: SVM·Bayesian·the deep learning revolution·Transformer (2000~2020)

- **2000s**: industrialization of SVM·random forest·gradient boosting
- **2006**: **Hinton DBN** — deep learning restarts
- **2009**: **ImageNet** dataset (Fei-Fei Li)
- **2010**: **GPU computation**·CUDA applied to ML in earnest
- **2012**: **AlexNet** wins ImageNet — *the deep learning revolution in earnest*
- **2014**: VAE·GAN (Goodfellow)
- **2015**: ResNet·Adam
- **2016**: AlphaGo (DeepMind)
- **2017**: **Transformer** (Vaswani et al. *Attention is All You Need*)
- **2018**: BERT·GPT-1
- **2020**: GPT-3 (175B)
- **2020**: AlphaFold 2
- **2020**: the Liquid tensor experiment challenge (Scholze + Lean)

→ Hinton 2006 → AlexNet 2012 → Transformer 2017 are the *three stages of the deep learning revolution*. AlphaGo·AlphaFold are the *RL·science applications*.

**Decisive turn**: Transformer 2017 + GPT-3 2020 → the *LLM era* begins. The *possibility of unifying all modalities* appears.

### Act 4 — Reinterpretation/application: LLM·AlphaProof·neuro-symbolic·MCP·agents (2020~present)

- **2022**: the ChatGPT explosion (GPT-3.5)
- **2022**: **completion of the Liquid tensor experiment** (Buzzard·Massot et al.)
- **2023**: GPT-4·Claude·Gemini·Llama
- **2023**: **AlphaProof + Lean** — IMO silver medal (DeepMind)
- **2024**: **AlphaProof** solves IMO 2024 P1·P2·P4·P6 (4/6)
- **2024**: o1·o3 reasoning models
- **2024**: **MCP** (Model Context Protocol, Anthropic) — the agent + tool standard
- **2025**: Claude Opus 4.x·Gemini 2·GPT-5 (1M context·multi-modal)
- **2025**: agent orchestration in earnest (Anthropic·OpenAI·Google) — **directly Dohun's work**
- **Present**: neuro-symbolic AI·verifiable AI·AI for science·AI for math in earnest

→ LLM + AlphaProof + MCP are the *full 21st-century form of AI*. The integration of Math (Lean·HoTT·Mathlib) + AI (LLM·reasoning) in progress.

**Decisive turn**: AlphaProof 2024 + Claude/GPT reasoning + MCP — *AI automates mathematics·CS·infrastructure alike* → this linkage is the *mathematical foundation* of Dohun's work (Cloud PM·AI systems).

### Core insight

The four acts of this linkage are the 90-year flow of *Math's formal limits = CS/AI's real limits*. Turing 1936 → Shannon 1948 → Cook-Levin 1971 → backprop 1986 → Transformer 2017 → AlphaProof 2024.

Meta-mechanism of this linkage: **CS/AI = the domain in which mathematics' tools of computability·information·probability·category are realized as *real systems***. This linkage is the *mathematical foundation* of Dohun's work (Cloud PM·AI systems·MCP·agent orchestration). Of *equal depth* to the PHYSICS bridges (quantum gravity in earnest) — the two deepest cases of *mathematics defining another domain*.

## Connections·follow-ups

- **Mathematics depth** → all Math foundations + modern + physics-bridges
- **Direct to the CS/AI industry** → Dohun's work (Cloud PM·AI systems·MCP·agents·LLM)
- **Related linkage** → Math ↔ Econ (the next note)
- **Related linkage** → Quantum Computing ↔ Complexity (P1, quantum computation ↔ CS) (not yet written)
- **Related linkage** → Witten-Kapustin 2007 — N=4 SYM ⟺ Geometric Langlands (HoTT·∞-category tools)
- **Meta-position** → **the most full-fledged case of mathematics defining a *real domain (CS/AI)*** — the linkage direct to Dohun's work. Input for the U-29·MM-16·R-14 candidate N≥3 verification (the formal-registration trigger fires when the 3 cases Math↔PHYSICS·Math↔CS/AI·Math↔Econ are reached)

## Workflow Connections
- index (the cross-domain-bridges meta-matrix)
- Math ↔ Econ
- Computability
- Probability
- infinity categories
- physics-bridges index
