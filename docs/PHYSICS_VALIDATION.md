# Validação física

Este documento registra **o que foi testado, como e com qual resultado** no núcleo físico Python (motor e analisador 1.1.0). Não é uma certificação de realismo: os modelos seguem a literatura, mas os parâmetros dos carros e pneus são estimativas e nada foi ajustado a telemetria real.

## 1. Dois tipos de teste

| Marcador pytest | Pergunta respondida | Exemplo |
| --- | --- | --- |
| `verification` | O código implementa corretamente a equação documentada? Referência analítica, autoconsistência, conservação, convergência numérica. | `|F| ≤ μ·Fz` para qualquer escorregamento; soma das cargas = peso; impulso de colisão conserva quantidade de movimento |
| `validation` | O comportamento emergente cai numa **faixa pública de plausibilidade**? A fonte e a natureza da faixa ficam na docstring do teste. | 0–100 km/h de um carro tipo F1 entre 2 e 4 s; desaceleração a 300 km/h entre 4 e 6,5 g |

Um teste de validação aprovado significa apenas “dentro da faixa”, não “igual ao carro real”. Testes longos têm também o marcador `slow`.

```bash
npm run test:python                         # tudo
cd python && python3 -m pytest -m verification
cd python && python3 -m pytest -m "validation and not slow"
```

## 2. Resultados das suítes

Execução local em 18/10/2026 (CPython 3.13, Node 22, Chromium 141 com SwiftShader), motor e analisador 1.1.0:

| Suíte | Comando | Resultado |
| --- | --- | --- |
| Componentes (atmosfera, pneu, aerodinâmica, trem de força, freios, suspensão) | `pytest tests/test_components.py` | 114 aprovados (verificação e plausibilidade) |
| Catálogo histórico e integridade do cache | `pytest tests/test_catalog.py` | 16 aprovados |
| Ponte Pyodide (contrato, spline idêntica ao Three.js, determinismo, CPython × Pyodide) | `npm test` | 16 aprovados em 3 arquivos (com os testes do esquema de circuitos e do benchmark gráfico) |
| Navegador real | `npm run test:e2e` | 4 aprovados (condução, editor, benchmark gráfico, benchmark do motor físico) |
| CI no GitHub | `.github/workflows/ci.yml` | jobs `python` e `test` verdes até o commit `b1355ef` |

Módulos pytest de dinâmica do veículo, clima/água, analisador de pistas, IA/corrida e ponte de sessão estão em elaboração e serão listados aqui quando aprovados.

## 3. Revisão adversarial: defeitos encontrados e corrigidos

Uma revisão independente tentou quebrar o motor com cenários reproduzíveis. Todos os defeitos abaixo foram reproduzidos antes da correção e verificados depois (scripts de reprodução fora do repositório; a maioria virou teste de regressão).

| Defeito | Efeito observado antes | Correção (versão 1.1.0) | Depois |
| --- | --- | --- | --- |
| Calor de flexão da carcaça 3× a potência de rolamento, sem limite | núcleo do pneu a 165–204 °C após 140 s no oval; grip 0,42–0,62; IA rodava | parcela além da resistência ao rolamento desaparece acima da janela do composto (`tyre.py::update_thermal`) | núcleo estabiliza entre 93 e 146 °C (oval e Aurora, 200 s); 0 toques em barreira |
| Percepção atrasada da IA comparada com a própria posição atual | carro 10 m à frente parecia 12 m atrás; 9–11 contatos na largada do oval | instantâneo atrasado extrapolado por `u·atraso` (`racing_ai/driver.py::Perception`) | 0 contatos no oval com 10 carros |
| Relógio da grade de água | chuva/secagem 1,07–1,88× mais rápidas que o tempo real | integra o tempo real entre visitas do segmento (`track.py::SurfaceState.update`) | 15 mm/h por 60 s → 0,2500 mm (esperado 0,2500) |
| Barreiras de trechos paralelos | barreiras de retas a 90 m se cruzavam (sem muro entre elas) | raio de busca da folga ≥ 2× o maior afastamento (`analyzer.py`, `geometry.py::clearances`) | 44,5 + 44,5 m < 90 m |
| Controle de velocidade da IA puramente proporcional | chegava às curvas acima do planejado; V10 na chuva rodava | antecipação pela inclinação do envelope de frenagem | ver seção 5 |
| Entrada não numérica | NaN/Infinity no JSON; `JSON.parse` falhava no navegador | erro de validação fatal; JSON estrito | mensagem de validação chega ao usuário |
| Atitude da pista com o carro de lado | carro a 90° numa inclinação de 10° desenhado plano (±0,49 m) | plano da pista lido no referencial do carro | igual à atitude exata |
| Trabalho O(comprimento) por passo | p99 de 2,18 ms num circuito de 14,9 km | média em cache por versão; grade de aderência da IA em fatias | p99 0,53 ms |
| Roda levantada (testes de componentes) | soma das cargas = peso + 137,6 N com a roda interna no ar | contato unilateral: carga do eixo vai para a outra roda | soma = peso |
| ERS no limitador | ERS seguia empurrando acima do corte de giro | limitador corta motor e ERS (dinâmica e envelope) | sem torque positivo no limitador |

## 4. Comparação CPython × Pyodide

- Cada runtime é **determinístico**: duas execuções com a mesma semente dão estados idênticos bit a bit (testado no Pyodide pela ponte Vitest e no CPython com `PYTHONHASHSEED` diferentes).
- Entre runtimes, as funções da `libm` (`sin`, `cos`, `atan2`, `exp`…) diferem no último bit. Em 5 s de condução em malha fechada com 2 carros na chuva, a diferença fica abaixo de 10⁻³ relativo (teste `tests/physics-bridge.test.ts`). Em corridas longas com muitos carros, trajetórias divergem (medido antes da 1.1.0: mesmas voltas, velocidade média a 0,12 km/h, um contato a mais no Pyodide numa corrida de 10 carros na chuva).
- Consequência: replays e comparações devem usar o mesmo runtime; o jogo usa sempre o Pyodide.

## 5. Robustez da IA (mesma física do jogador)

Sementes fixas, CPython 3.13, motor 1.1.0.

**Sozinho no Aurora (100 s):** 3 carros × 4 climas (seco, chuva leve, chuva intensa, secando) × níveis Fácil e Realista = 24 combinações, **0 eventos de barreira e 0 s fora da pista** em todas. Melhores voltas do carro de 2022: 29,0 s (Fácil) e 23,9 s (Realista) no seco; 28,6 s (Realista) na chuva intensa.

**Corridas de 10 carros (60 s, 3 sementes):**

| Condição | Contatos por corrida | Eventos de barreira | Dano máximo |
| --- | --- | --- | --- |
| Seco, Fácil | 4 / 0 / 3 | 0 / 0 / 0 | 0,02 |
| Seco, Médio | 1 / 0 / 1 | 0 / 0 / 0 | 0,00 |
| Seco, Realista | 1 / 1 / 2 | 0 / 0 / 0 | 0,00 |
| Chuva leve, Difícil | 2 / 8 / 1 | 0 / 0 / 0 | 0,01 |
| Chuva intensa, Realista | 1 / 0 / 0 | 2 / 0 / 0 | 0,00 |
| Secando, Intermediário | 2 / 1 / 1 | 0 / 0 / 0 | 0,00 |

Antes das correções de pilotagem: seco/Médio 9 / 3 / 1 contatos; chuva intensa/Realista 11 / 17 / 15 eventos de barreira; o turbo de 1986 não completava voltas com chuva leve (pneu slick num carro sem intermediário). No oval de 3,3 km com 10 carros (Médio, seco, 40 s): 0 contatos nas três sementes.

## 6. O que não foi validado

- Comparação com telemetria, dados de equipes ou tempos de volta reais — os carros são estimativas por época.
- A linha de corrida é uma linha inicial de curvatura mínima, não um ótimo de tempo de volta.
- Desempenho na RTX 3050 Laptop, 1080p/60 FPS no perfil Alto e gamepad físico.
- Aquaplanagem segue a lei de Horne & Dreher para pneus de avião/caminhão; a transposição para pneus de corrida é uma aproximação.
- Ultrapassagens, defesa e estratégia são heurísticas determinísticas; não há modelo de pit stop.
