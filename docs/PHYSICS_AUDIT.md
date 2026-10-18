# Auditoria da física anterior (TypeScript)

Auditoria do APEX Racing Lab **antes** da migração para o núcleo Python, feita sobre o commit `d610e82` (merge do PR #1). Todos os resultados abaixo vêm de execuções reais neste ambiente de nuvem; nada foi estimado sem medição, e afirmações derivadas apenas da leitura do código estão marcadas como tal.

## 1. Escopo e método

Arquivos lidos integralmente: `src/racing/simulation.ts`, `src/circuits/geometry.ts`, `src/circuits/model.ts`, `src/circuits/editor.ts`, `src/graphics/world.ts`, `src/graphics/car.ts`, `src/graphics/benchmark.ts`, `src/graphics/quality.ts`, `src/graphics/gpu.ts`, `src/main.ts`, `src/core/types.ts`, `src/storage/database.ts`, `tests/*.test.ts`, `tests/e2e/workflow.spec.ts`, configurações (Vite, Vitest, Playwright, TypeScript) e workflows de CI.

Ambiente: Linux na nuvem, Node.js 22.22 (o `package.json` pede Node 24; o `npm ci` só emite aviso `EBADENGINE`), npm 10.9, Python 3.13, Chromium 1194 do Playwright com WebGL 2 por SwiftShader (renderizador por software — não representa a RTX 3050 Laptop).

## 2. Resultados das verificações existentes (linha de base)

| Verificação | Comando | Resultado |
| --- | --- | --- |
| Instalação | `npm ci` | OK (aviso de engine Node 24 × 22) |
| Tipos/lint | `npm run lint` | OK, sem erros |
| Testes unitários | `npm test` | **22 aprovados** em 3 arquivos (1,75 s) |
| Build | `npm run build` | OK; aviso de chunk do Three.js (~500 kB) |
| E2E no Chromium | `CHROMIUM_PATH=… npx playwright test` | **3 aprovados** (condução 30,6 s; benchmark 54,6 s; editor 25,5 s) |

Observação: a configuração do Playwright usa `/usr/bin/chromium`, que não existe neste ambiente; foi necessário apontar `CHROMIUM_PATH` para o Chromium do Playwright.

## 3. Arquitetura física anterior

- Toda a física, a IA, a cronometragem e as colisões ficavam em `src/racing/simulation.ts` (552 linhas), executando **no thread principal**, junto com a renderização.
- Integração: passo fixo de 1/120 s. O laço do `main.ts` acumulava o tempo real (`accumulator += Math.min(raw, 5)`) e chamava `update(1/120)` em laço; `update(dt)` ainda subdividia qualquer `dt` em passos ≤ 1/120 s com teto de 0,25 s.
- Unidades: metros e m/s, ângulo de rumo com 0 = +z e frente = (sin ψ, cos ψ).

## 4. Fórmulas existentes (inventário)

Referências de linha em `src/racing/simulation.ts` no commit auditado.

| Fenômeno | Fórmula no código | Avaliação |
| --- | --- | --- |
| Limite de esterço | `δmax = 0,5 / (1 + v/38)` (l. 175–177), aplicado a jogador **e** IA | Filtro de jogabilidade tratado como física; reduz o esterço físico de todos os carros |
| Aderência | `grip = (chuva ? 0,62 : 1)·(grama ? 0,29 : 1)·(1 − 0,0035·desgaste)·(1 − 0,0015·dano)` (l. 178–185) | Multiplicadores globais constantes; chuva igual em toda a pista |
| Limite lateral | `a_lat,max = (13,8 + 0,004·v²)·grip` m/s² (l. 195) | Constante ad hoc (≈1,4 g + termo quadrático); sem carga, pneus ou aerodinâmica explícitos |
| Guinada | modelo de bicicleta cinemático `r = v/L·tan δ`, L = 3,6 m, saturado em `a_lat,max/v` (l. 197–204) | Sem dinâmica de pneus nem inércia de guinada |
| Deriva lateral | `v_lat += (−excesso·v·0,15 − v_lat·(2,5 \| 7)·grip)·dt`, limitado a ±7 m/s (l. 205–210) | Heurística sem base física |
| Arrasto | `a = v²·(0,0015 \| 0,0011 com DRS)` m/s² (l. 213) | Sem densidade do ar, área, massa ou Cd explícitos |
| Motor | `a = 17·max(0,1, 1 − v/180)·(1 − 0,005·dano)` m/s² (l. 214–217) | Sem torque, rotação, marchas ou potência; marcha/RPM eram apenas cosméticas (l. 263–276) |
| Rolamento | `0,45` m/s² (grama: `4,8 + 0,08·v`) (l. 218–219) | Constante, não proporcional à carga |
| Rampa | `a = −9,81·Δy/5` (l. 220) | Aproximação aceitável, mas sem efeito na carga vertical |
| Frenagem | `a = 24·freio·min(grip, 1,2)` m/s² (≈2,4 g máx.) (l. 224) | **Independente da curva**: frear e virar não disputavam o mesmo limite; sem transferência de carga, sem travamento, sem downforce |
| Velocidade | limitada a `[0, 105]` m/s (l. 221–231) | Sem marcha à ré; velocidade máxima imposta |
| Barreira | correção de posição, `dano += 0,045·v_n`, `v *= max(0,35, 1 − 0,025·v_n)`, rumo puxado à tangente (l. 242–261) | Não conserva quantidade de movimento; sem rotação |
| Contato entre carros | círculos de raio 2,15 m, massas iguais, impulso `0,575·v_rel` aplicado ao longo do rumo (l. 375–420) | Sem rotação, sem restituição explícita |
| Combustível/desgaste | taxas fixas por segundo (l. 236–241) | Sem efeito de massa |
| IA | `v_curva = √(13,8·grip / (κ·seg − 0,004·grip))`, frenagem `18·grip/seg`, alvo à frente, ruído senoidal de faixa (l. 279–373) | Tabela implícita pela mesma constante ad hoc; IA e jogador com limites diferentes |
| Geometria | Catmull-Rom centrípeta do Three.js, 768 amostras por arco; curvatura por três pontos; projeção em janela de ±18 amostras | Base geométrica sólida e reaproveitada |

## 5. Problemas identificados

1. **Direção do jogador invertida (derivado do código e verificado numericamente).** `readInput()` produz `steer = +1` para a tecla da direita; na simulação `+1` aumenta o rumo ψ, girando o carro para +x. Para um carro voltado para +z, +x é o lado **esquerdo** (direita = frente × cima = −x). Verificação: com `steer = +1` por 0,5 s a 20 m/s, ψ variou +0,356 rad e x +1,71 m. A IA era coerente consigo mesma, o que mascarava o problema nos testes.
2. Frenagem e aceleração lateral sem círculo de atrito; frear em curva não reduzia a capacidade de curva.
3. Nenhum modelo de pneus (escorregamento, carga, temperatura, pressão); desgaste não alterava a dinâmica além de um fator linear.
4. Ausência de transferência de carga, suspensão, inércia de guinada e forças por roda.
5. Aerodinâmica embutida em constantes de aceleração; o downforce só existia como termo `0,004·v²` do limite lateral e não afetava a frenagem.
6. Motor e câmbio cosméticos; marcha à ré inexistente.
7. Chuva como multiplicador global 0,62 (sem água espacial, drenagem, poças ou aquaplanagem).
8. **Barreiras física × visual incoerentes:** a física parava os carros a `largura/2 + 3 m`, mas os trilhos eram desenhados a `largura/2 + 6,5 m` (`world.ts`), deixando 3,5 m de “parede invisível”.
9. DRS do jogador nunca acionável (`readInput()` sempre enviava `drs: false`).
10. Física no thread principal e recuperação de atraso de até 5 s em um único quadro (até 600 passos), com risco de travamento perceptível.
11. Colisões sem conservação de quantidade de movimento nem rotação.
12. A validação geométrica do editor (TypeScript) e a física eram implementações separadas, sem análise física derivada da pista (curvas, frenagem, linha de corrida, perfis de velocidade).

## 6. Desempenho medido do modelo anterior

Modelo TypeScript antigo, Node 22 (V8 JIT), circuito Aurora Park, 6.000 passos após aquecimento:

| Carros | ms por passo | µs por carro |
| --- | --- | --- |
| 2 | 0,044 | 21,9 |
| 6 | 0,116 | 19,3 |
| 10 | 0,175 | 17,5 |

É muito barato porque é um modelo de bicicleta cinemático com constantes; serve de referência de custo, não de fidelidade.

## 7. Conclusões que orientaram a reestruturação

- Reprojetar (não traduzir) a física: modelo de quatro rodas, pneus com elipse de atrito, aerodinâmica parametrizada, trem de força real, freios, suspensão, água espacial e colisões com impulso e rotação — ver `PHYSICS_ARCHITECTURE.md` e `PHYSICS_EQUATIONS.md`.
- Tornar Python a fonte de verdade e tirar a física do thread principal (Pyodide em Web Worker) — ver `PYTHON_BROWSER_INTEGRATION.md`.
- Gerar todos os dados físicos derivados da pista por um analisador único, usado igualmente por pistas criadas, importadas e históricas — ver `TRACK_ANALYSIS.md`.
- Corrigir o sinal da direção, a incoerência das barreiras e o DRS do jogador.
- Validar com testes que separam verificação de código e plausibilidade física — ver `PHYSICS_VALIDATION.md`.
