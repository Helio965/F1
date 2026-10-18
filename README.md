# APEX — Racing Lab

Laboratório de corridas 3D para Chrome: desenhe um circuito, analise e homologue a pista, meça o desempenho e pilote contra adversários controlados por IA. Interface em português, renderização em Three.js r170 (TypeScript + Vite).

Desde a versão 1.0.0 do motor, **toda a física, a análise de pistas, a IA e o clima são implementados em Python** (`python/`), que é a única fonte da verdade. No navegador, esse mesmo código Python roda dentro do **Pyodide (CPython compilado para WebAssembly) em um Web Worker**; o Three.js apenas desenha os estados que a física produz. Não há servidor de física nem requisições HTTP durante a corrida. Black Hole foi analisado exclusivamente como referência; seus arquivos não foram alterados.

Não é um simulador profissional: os modelos são documentados, verificados e plausíveis, mas não foram calibrados com telemetria real (veja [Limites](#limites-e-pendências)).

## Executar

Requisitos: **Node.js 24** e npm; Chrome recente com **WebGL 2**. Para os testes do núcleo físico: **Python 3** com `pytest` (o código usa só a biblioteca padrão; testado com CPython 3.13, e com Python 3.14 via Pyodide). Nenhuma chave de API, banco remoto ou conta é necessária.

```bash
npm ci            # instala dependências (inclui o pacote npm do Pyodide)
npm run dev       # copia o runtime Pyodide para public/pyodide e abre o Vite
```

Produção: `npm run build` gera `dist/` (inclui o runtime Pyodide, ~13 MB, servido pela mesma origem); sirva a pasta por HTTP. `npm run preview` testa o build localmente. O aplicativo não funciona abrindo `index.html` pelo sistema de arquivos (Web Workers de módulo exigem HTTP). Na primeira visita o motor físico leva alguns segundos para carregar (medido: ~3,4 s no Chromium de teste); depois disso a corrida funciona offline enquanto a página estiver aberta.

## Arquitetura

```
 editor / importação ──► documento do circuito (JSON, esquema 2)
                               │
            Web Worker (Pyodide = CPython em WebAssembly)
   ┌───────────────────────────┴──────────────────────────────┐
   │ track_analyzer ─► análise + hash ─► apex_bridge.Session  │
   │                                       │                  │
   │ physics_engine (passo fixo 1/120 s) ◄─┴─► racing_ai      │
   └───────────────────────────┬──────────────────────────────┘
          snapshot float64 versionado (estado anterior + atual)
                               ▼
     thread principal: interpolação visual + Three.js (world.ts, car.ts)
```

| Pacote | Conteúdo |
| --- | --- |
| `python/physics_engine/` | Veículo de quatro rodas (3 GDL planares + arfagem/rolagem/heave da suspensão, transferência de carga), pneus (Magic Formula normalizada, elipse de atrito, sensibilidade à carga, temperatura em dois nós, pressão, desgaste, água/aquaplanagem), aerodinâmica (downforce dianteiro/traseiro, efeito solo, asas, DRS, esteira), trem de força (curva de torque, marchas, embreagem, freio-motor, limitador, diferencial, ERS, combustível), freios (torque por eixo, balanço ajustável, travamento, temperatura), água espacial por segmento/faixa, colisões com impulso e rotação, cronometragem, envelope quase-estático |
| `python/track_analyzer/` | Spline Catmull-Rom idêntica à do Three.js, amostragem adaptativa, curvatura/raio/rampa/inclinação, validação com códigos, curvas e retas, zebras/escapes/barreiras, linha de corrida, perfis de velocidade por carro e condição, zonas de frenagem/aceleração, riscos, hash de cache, catálogo histórico com procedência |
| `python/racing_ai/` | Percepção com atraso, plano de velocidade, Pure Pursuit, controle de pedais na elipse de atrito, ultrapassagem/defesa, estratégia de ritmo, cinco níveis |
| `python/apex_bridge/` | Sessão de passo fixo, API usada pelo Worker, benchmarks |
| `src/physics/` | Worker, cliente com contrapressão, contrato do snapshot, protocolo de mensagens |

Documentação técnica:

- [Auditoria da física anterior](docs/PHYSICS_AUDIT.md) — o que existia, problemas encontrados, custo medido.
- [Arquitetura física](docs/PHYSICS_ARCHITECTURE.md) — módulos, fluxo de dados, IA, determinismo, versões.
- [Equações](docs/PHYSICS_EQUATIONS.md) — modelos, unidades SI e convenções de sinais.
- [Análise de pistas](docs/TRACK_ANALYSIS.md) — entradas, saídas, validação, cache, catálogo histórico.
- [Integração Python/navegador](docs/PYTHON_BROWSER_INTEGRATION.md) — Pyodide, Worker, contrato, alternativas avaliadas, desempenho.
- [Validação física](docs/PHYSICS_VALIDATION.md) — verificação de código × plausibilidade física, resultados reais.

## O que funciona

- **Editor** por mouse ou toque: circuito fechado, pontos móveis, largura, elevação, **inclinação lateral por ponto**, tipo de asfalto, tipo e largura da área de escape; desfazer/refazer; prévia 3D. **Análise rápida** em Python enquanto se edita; **análise completa** com relatório (curvas, retas, riscos, tempos de referência seco/molhado), volta de teste e **homologação vinculada ao hash da análise**. Editar a pista revoga a homologação.
- **Corrida** com 2–10 carros, três épocas de carro (estimativas de engenharia, não especificações oficiais), cinco climas (seco, noite seca, chuva leve, chuva intensa e pista secando), cinco níveis de IA (Fácil, Intermediário, Médio, Difícil, Realista), assistências de tração/ABS (completa, média, desligada) e **balanço de freio ajustável**. A IA usa exatamente a mesma física e os mesmos parâmetros do carro do jogador.
- **Chuva espacial**: lâmina d'água por segmento e faixa, drenagem pela inclinação, poças em pontos baixos, secagem e linha seca pelo tráfego; o renderizador desenha o molhado/poças e o spray a partir dos dados da física.
- **HUD** com marcha, RPM, combustível, temperaturas dos pneus, ERS, DRS e custo/latência da física; minimapa; câmeras; pausa; teclado reatribuível, gamepad padrão e botões de toque.
- **Benchmarks**: gráfico (FPS médio/mínimo, 1% low, p95, picos; [método](docs/BENCHMARK.md)) e **do motor físico no próprio navegador** (passo médio/p95/p99/máximo, custo por carro, parcela da IA, carga em tempo real, análise de pista, memória e latência ao Worker).
- **IndexedDB** para circuitos, análises em cache (chave: documento + versões do motor e do analisador), resultados, preferências e recordes; importação JSON validada, sem executar conteúdo importado.

### Controles

| Ação | Tecla padrão |
| --- | --- |
| Acelerar / frear | W / S ou ↑ / ↓ |
| Direção | A / D ou ← / → |
| Marcha à ré | segurar o freio com o carro parado |
| DRS (quando permitido) | E |
| Balanço de freio para trás / frente | [ / ] |
| Alternar câmera | C |
| Reposicionar | R |
| Exibir FPS | F |
| Pausar | Esc |

Em Ajuda é possível reatribuir as teclas de condução. Gamepad: eixo esquerdo + RT/LT.

## Testes

```bash
npm run lint          # TypeScript estrito
npm test              # Vitest: esquema dos circuitos, benchmark, ponte Pyodide (carrega o Pyodide real,
                      # verifica o contrato, a equivalência da spline com o Three.js e CPython × Pyodide)
npm run test:python   # pytest do núcleo físico (python/tests); marcadores: verification, validation, slow
npm run build         # tipos + produção
npm run test:e2e      # Chromium real: condução, editor, benchmark gráfico e benchmark do motor físico
npm run bench:physics # benchmark do núcleo Python no Pyodide (Node); --compare roda também o CPython nativo
```

`npm run test:python` exige `pytest` (`python3 -m pip install pytest`). Os testes `validation` comparam comportamento emergente com faixas públicas de plausibilidade; não são calibração. Os testes de navegador usam o Chromium do Playwright em CI; localmente defina `CHROMIUM_PATH` se `/usr/bin/chromium` não existir. O renderizador por software da nuvem não representa a RTX 3050 Laptop.

CI: `.github/workflows/ci.yml` (job Python + job Node/Chromium). A publicação Pages é manual (`.github/workflows/pages.yml`).

## Limites e pendências

- **Fidelidade**: os modelos seguem a literatura (Pacejka, Milliken, Horne & Dreher, Manning, ISA), mas os parâmetros dos carros e pneus são estimativas plausíveis, não dados de equipes; nada foi ajustado a telemetria. Os testes de validação verificam faixas de plausibilidade, não realismo profissional.
- **Pistas históricas**: o analisador aceita qualquer documento válido e `track_analyzer/catalog.py` define registros com procedência, campos estimados e estado de validação calculado. **Nenhuma geometria de circuito real acompanha o projeto** e ainda não há interface para importar registros do catálogo. Os cartões de Monza, Interlagos e Suzuka continuam apenas informativos.
- **IA**: completa corridas sem ajuda, mas em chuva com os carros V10 e turbo ainda toca barreiras ocasionalmente; a linha de corrida é uma aproximação suave, não uma otimização de tempo mínimo.
- **Desempenho**: medido em CPU de nuvem e no Chromium headless (ver documentação); a RTX 3050 Laptop e 1080p/60 FPS no perfil Alto **não foram medidos**. O Pyodide é ~2× mais lento que o CPython nativo; não foi necessária extensão compilada para 10 carros a 120 Hz nas medições feitas.
- **Determinismo**: cada runtime é determinístico; CPython nativo e Pyodide divergem no último bit de funções da `libm`, o que separa trajetórias longas em corridas com muitos carros (diferenças visuais desprezíveis nos testes).
- Não implementado: pit stops e estratégia de boxes, regras/bandeiras oficiais, classificação, replay, pit lane e obstáculos editáveis, tangentes Bézier, neve/neblina/ciclo horário, WebGPU e pós-processamento avançado.

Consulte [a análise técnica do Black Hole](docs/BLACK_HOLE_ANALYSIS.md), [as referências visuais](docs/REFERENCE_NOTES.md), [as licenças](docs/ASSETS.md) e [o registro de validação](docs/VALIDATION.md). Licença do projeto: MIT.
