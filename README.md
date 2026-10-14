# APEX — Racing Lab

Primeira versão jogável de um laboratório de corridas 3D para Chrome, implementada neste repositório. Desenhe um circuito, valide a geometria, homologue uma versão, execute um benchmark real e pilote contra adversários. Interface em português, Three.js r170, TypeScript e Vite. Black Hole foi analisado exclusivamente como referência; seus arquivos não foram alterados.

## Executar

Requisitos: **Node.js 24**, npm e Chrome recente com **WebGL 2 / aceleração gráfica**. Nenhuma chave de API, banco remoto ou conta de jogo é necessária. Os assets e as fontes são locais após a instalação.

```bash
npm ci
npm run dev
```

Abra o endereço exibido pelo Vite no Chrome local. Para produção: `npm run build`; sirva a pasta `dist` por HTTP. `npm run preview` testa esse build localmente. O aplicativo não funciona abrindo `index.html` diretamente pelo sistema de arquivos.

## O que já funciona

- Menu e diagnóstico real da GPU/contexto, classificação aproximada, perfis Baixa/Média/Alta, escala de resolução e adaptação com intervalo mínimo entre reduções. Sem preferências salvas, renderizadores por software iniciam em Baixa com escala de 60%. O perfil Baixa usa iluminação por vértice; Média/Alta usam PBR. `?quality=low|medium|high` força um perfil e suspende a adaptação.
- Benchmark com dez carros e clima selecionado, compilação de shaders, aquecimento separado, medição mínima de 30 segundos, FPS médio/mínimo, 1% low, mediana, p95 e picos. Histórico, exportação JSON e recomendação sem inventar desempenho. Aba oculta, resize e perda de contexto invalidam a medição. [Método completo](docs/BENCHMARK.md).
- Editor 2D por mouse ou toque: desenho livre de circuito fechado, pontos móveis, inserção/remoção, largura, elevação, desfazer/refazer e prévia 3D. Curva Catmull-Rom centrípeta com 768 amostras por comprimento de arco.
- Validação de largura, extensão, cruzamentos, proximidade entre trechos, curvas e inclinações. Grid e checkpoints automáticos. Homologação explícita vinculada à versão; editar a estrutura exige nova validação.
- Corrida com 2–10 carros, número de voltas, cinco níveis de IA e dia/noite/chuva. Condução, frenagem, aderência molhada, arrasto, downforce aproximado, impactos simples, combustível e desgaste. Física em passos de 1/120 s, interpolação visual e contagem de voltas por setores ordenados.
- IA utiliza direção, previsão de curvatura, frenagem antecipada e escolha de faixa no tráfego. Níveis alteram reação, margem de segurança e precisão. O rótulo Realista permanece experimental.
- Câmeras externa/cockpit/traseira/transmissão, velocidade, marcha, RPM, posição, tempos, condições do carro, minimapa, FPS e pausa. Teclado reatribuível, gamepad padrão e botões de toque.
- IndexedDB para versões dos circuitos, resultados, preferências, configurações de corrida e recordes; importação JSON com esquema validado, sem executar conteúdo importado.

### Controles

| Ação                        | Tecla padrão   |
| --------------------------- | -------------- |
| Acelerar / frear            | W / S ou ↑ / ↓ |
| Direção                     | A / D ou ← / → |
| Freio                       | Espaço         |
| Alternar câmera             | C              |
| Reposicionar no ponto local | R              |
| Exibir FPS                  | F              |
| Pausar                      | Esc            |

Em Ajuda é possível reatribuir W/A/S/D. Gamepad: eixo esquerdo + RT/LT. Não existe um modo de piloto automático para o jogador.

## Limites desta entrega

É uma base jogável de desenvolvimento, **não um simulador profissional nem uma reprodução visual de F1 25**. O modelo de bicicleta e colisões por aproximação geométrica não resolvem suspensão, quatro pneus individuais ou dinâmica completa de corpos rígidos. Combustível, danos e desgaste são modelos iniciais. Os carros, texturas e cenários são procedurais originais; não há modelos GLB de alta fidelidade, fotogrametria ou marcas de equipes.

Ainda faltam edição de tangentes Bézier/inclinação lateral, pit lane editável, obstáculos editáveis, áreas de escape configuráveis, suspensão e pneus avançados, aquaplanagem, regras/bandeiras oficiais, classificação, estratégia de boxes, replay e câmera livre. WebGPU, SSAO, bloom, motion blur, LOD de modelos externos e clima completo (neve/neblina/ciclo horário) não foram implementados. Noite e chuva alteram iluminação/materiais/partículas; a chuva também altera a física. Os reflexos usam um ambiente gerado, sem espelhamento físico da cena.

O arquivo histórico contém apenas referências informativas a Monza, Interlagos e Suzuka. Não é o catálogo completo de 1980 ao presente; não há geometria precisa licenciada e esses cartões não permitem correr. Aurora Park é um circuito fictício original. Nenhuma imagem ou modelo das referências do usuário foi incorporado ao jogo.

A RTX 3050 Laptop de 6 GB é uma meta de teste local. A nuvem não mede essa GPU; nenhuma taxa de quadros nela é garantida. Privacidade do navegador pode limitar identificação da GPU. WebGPU exposta não significa backend ativo; VRAM física permanece indisponível.

## Testes

```bash
npm run lint       # TypeScript estrito e verificação de símbolos não utilizados
npm test           # benchmark, esquema/geometria e simulação determinística
npm run build      # tipos e produção
npm run test:e2e    # Chromium real: condução, salvamento e benchmark completo
```

Na nuvem, os testes de navegador usam `/usr/bin/chromium` com SwiftShader. Em outras máquinas, defina `CHROMIUM_PATH` para seu Chrome/Chromium ou rode em CI, onde o Playwright instala seu navegador. O renderizador de software não é uma medição da GPU do notebook. Os testes funcionais não falsificam o canvas ou resultados de benchmark.

CI está em `.github/workflows/ci.yml`. A publicação Pages é **manual** em `.github/workflows/pages.yml`: após revisar/mesclar, configure Settings → Pages → GitHub Actions e execute o workflow na branch desejada. O build usa caminhos relativos, inclusive após renomear o repositório. Estes arquivos não significam que o site já foi publicado.

Resultados da execução: [docs/VALIDATION.md](docs/VALIDATION.md).

## Arquitetura e progresso

| Área                            | Módulo                                 | Estado                                                |
| ------------------------------- | -------------------------------------- | ----------------------------------------------------- |
| Inicialização, menus e HUD      | `src/main.ts`, `src/style.css`         | Primeira versão                                       |
| Renderização e assets originais | `src/graphics/world.ts`, `car.ts`      | Procedural; sem fidelidade fotográfica                |
| Capacidades e desempenho        | `gpu.ts`, `benchmark.ts`, `quality.ts` | Implementado com limites das APIs documentados        |
| Edição e validação              | `src/circuits/`                        | Núcleo 2D/3D; ferramentas avançadas pendentes         |
| Física, IA, cronometragem       | `src/racing/simulation.ts`             | Aproximação inicial jogável                           |
| Dados locais                    | `src/storage/database.ts`              | IndexedDB; exporte JSON para backup                   |
| Fases avançadas 6–9 do pedido   | `docs/` + workflows                    | Gráficos avançados, catálogo e regras ainda pendentes |

A aplicação usa o checkout existente do ambiente; tarefas na nuvem já são isoladas e não precisam de um Git worktree adicional.

Consulte [a análise técnica do Black Hole](docs/BLACK_HOLE_ANALYSIS.md), [as referências visuais](docs/REFERENCE_NOTES.md) e [as licenças dos assets](docs/ASSETS.md). Licença do projeto: MIT.
