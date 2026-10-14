# Análise técnica do Black Hole

Análise realizada em 14 de outubro de 2026 sobre o repositório [Helio965/Black-Hole](https://github.com/Helio965/Black-Hole), commit [`6370fc7bb7af388c9dd96ae3b2cdf68b391fe78b`](https://github.com/Helio965/Black-Hole/tree/6370fc7bb7af388c9dd96ae3b2cdf68b391fe78b). O checkout de referência foi apenas lido. As conclusões abaixo vêm da inspeção do código; não representam medições de desempenho na RTX 3050.

## Dependências e inicialização

- `index.html:13–19` fixa Three.js **0.170.0** e os addons correspondentes via import map do jsDelivr. A aplicação original usa módulos JavaScript sem etapa de build.
- `js/main.js:37–55` cria `WebGLRenderer`, pede `powerPreference: 'high-performance'`, usa `antialias: false` e `NeutralToneMapping`, e apresenta fallback quando a criação do renderizador falha. A preferência de energia é uma solicitação ao navegador, sem garantia de seleção da GPU dedicada.
- Three.js r170 estabelece WebGL 2 como requisito deste caminho de renderização. Não há backend WebGPU na referência.
- `iniciar.bat` chama `tools/servidor.ps1`, que serve arquivos por HTTP local e pode abrir Chrome/Edge com perfil separado. O script também oferece configurar a preferência de GPU no registro do Windows e usa `--force-high-performance-gpu`. Esses mecanismos são específicos do ambiente local e não foram executados ou transferidos para o novo projeto.
- `index.html:122–143` explica por que a aplicação deve ser servida por HTTP em vez de abrir o HTML por `file://`.

## Diagnóstico da GPU

`js/gpu.js:16–31` consulta o contexto WebGL. Quando disponível, usa `WEBGL_debug_renderer_info` e `UNMASKED_RENDERER_WEBGL`; caso contrário, usa `RENDERER`. Erros de consulta resultam em nome desconhecido.

As expressões regulares em `gpu.js:11–13` classificam a string como software, dedicada, integrada ou outra. `cleanName()` remove parte dos detalhes de ANGLE e do backend para facilitar a leitura. Essa classificação é **heurística**: a extensão pode ser bloqueada por privacidade e nomes de fabricantes não são prova de arquitetura, memória ou desempenho. Strings como SwiftShader e llvmpipe são indícios úteis de renderização por software.

A referência não consulta VRAM física. O novo jogo deve preservar essa distinção: nome e limites expostos pelo contexto são dados observados; tipo de GPU é uma aproximação; 6 GB da RTX 3050 Laptop é o hardware de referência informado pelo usuário. A aplicação não deve apresentar essa capacidade como memória detectada.

## Perfis de qualidade verificados

Valores literais de `js/quality.js:9–15`:

| Perfil | Partículas | Estrelas | Teto de pixel ratio | Segmentos por trilha |
| ------ | ---------: | -------: | ------------------: | -------------------: |
| Low    |     20.000 |    1.000 |                 1,5 |                    6 |
| Medium |     36.000 |    1.400 |                1,25 |                    6 |
| High   |     60.000 |    1.800 |                   2 |                    6 |
| Ultra  |    110.000 |    2.200 |                   2 |                    8 |

O teto de resolução não é monotônico: Low permite mais pixels que Medium em telas de DPR alto. Não convém reproduzir essa ordem no jogo de corrida.

`detectQualityProfile()` aceita `?quality=ultra|high|medium|low`, desativando adaptação ao forçar um perfil. Sem override, software começa em Low com DPR máximo 1; ponteiro grosso em tela pequena escolhe Low; integrada escolhe Medium; dedicada escolhe Ultra; desconhecida escolhe High. O nome de uma GPU, isoladamente, não é suficiente para recomendar Alta em uma cena diferente.

No jogo, os orçamentos precisam se referir a sombras, resolução, cenário, chuva e detalhes dos carros. Contagens de partículas de um disco de acreção não fornecem orçamento de geometria ou texturas para um grid.

## FPS e adaptação

`createFrameRateGovernor()` em `quality.js:40–83` usa:

- alvo de 45 FPS;
- aquecimento de 1,5 segundo;
- janelas de amostragem de 1,5 segundo;
- no máximo cinco reduções durante a vida do governador;
- FPS calculado como número de quadros dividido pelo tempo acumulado.

Em `main.js:224–239`, a primeira reação é reduzir o pixel ratio em passos de 0,5 até 1. Depois, reduz o orçamento visível de partículas para 65% do anterior, com piso de 10.000. Há espera para estabilização após cada redução, mas não existe política completa de subida/descida com limiares distintos, recuperação ou histórico de decisões.

O HUD calcula FPS em janelas de aproximadamente 0,5 segundo (`main.js:245–258`). Isso mede a cadência de `requestAnimationFrame`; não é tempo exclusivo de GPU. A referência não calcula 1% low, percentis, estabilidade, relatório persistente ou benchmark pré-jogo.

O evento `visibilitychange` zera os acumuladores do governador, mas não redefine explicitamente o relógio de Three.js. A primeira amostra ao voltar de uma aba oculta pode incluir o intervalo oculto. No novo benchmark, ocultar a aba deve invalidar o ensaio e a retomada deve estabelecer um novo timestamp.

## Pós-processamento e shaders

`main.js:166–179` configura um render target HalfFloat, seguido por `RenderPass → UnrealBloomPass → OutputPass`. O bloom usa pesos próprios nos níveis de desfoque; `keepShadowBlack()` substitui o shader de composição para mascarar o centro do buraco negro. Essa máscara pertence à direção artística da cena espacial e não deve ser transportada para asfalto e carros.

`js/shaders.js` contém anel de fótons, noise simplex, lente gravitacional aproximada, disco, estrelas e composição do bloom. As órbitas, turbulência e cores das trilhas são calculadas na GPU a partir de atributos estáticos e uniforms de tempo. Há descarte antecipado de imagens secundárias pouco visíveis, limites numéricos para evitar divisões instáveis e clamp antes de `pow()` para evitar NaN.

Conceitos úteis para a corrida: efeitos repetitivos na GPU, chuva com orçamento limitado, materiais compartilhados, limites numéricos e exposição controlada. Lente gravitacional, Doppler artístico e compensação de brilho das partículas não modelam iluminação ou dinâmica de veículos.

## Memória e recursos

`accretionDisk.js:94–143` usa uma `InstancedBufferGeometry`, dois atributos de quatro floats por partícula e geometria base pequena. Os dois meshes das imagens da lente compartilham a geometria. `setVisibleCount()` altera `instanceCount`, evitando realocar buffers por quadro.

Essa redução diminui trabalho de desenho, **sem liberar os buffers originalmente alocados**. Os oito floats representam 32 bytes de atributos por partícula: cerca de 3,36 MiB para 110.000 partículas, antes de geometria base, cópias do driver, render targets e demais recursos. Isso é uma conta dos arrays, não uma medição de VRAM.

`stars.js` também pré-aloca arrays tipados e usa um único objeto de pontos. Geradores com seed tornam disco e estrelas repetíveis. Em `main.js:203–217`, renderizador e composer recebem o mesmo DPR limitado, evitando resoluções inconsistentes. Render targets crescem aproximadamente com o quadrado do DPR; DPR 2 produz quatro vezes os pixels de DPR 1 na mesma área CSS.

Não foi encontrado gerenciamento explícito de descarte de geometria, materiais, composer ou listeners, nem recuperação de contexto WebGL. Uma cena única tolera esse ciclo de vida mais simples; um jogo com trocas de pista precisa liberar recursos de cada cena e tratar contexto perdido. Não há monitoramento de heap, orçamento de textura ou streaming de assets na referência.

## Adaptação para o novo projeto

1. Manter renderizador, física, IA, editor, armazenamento e benchmark separados. Adotar TypeScript/Vite e dependências locais fixadas pelo lockfile.
2. Reutilizar conceitualmente diagnóstico WebGL, preferência de alto desempenho, presets e limite de DPR. Não copiar o loop da cena espacial como física veicular: simulação deve usar passo fixo e interpolação.
3. Usar pista, carros e condições reproduzíveis para benchmark. Aquecer shaders antes da coleta e manter o perfil fixo durante a medição.
4. Guardar tempos individuais de quadro. Documentar FPS médio como `1000 / média(dt em ms)`; mínimo como `1000 / maior dt`; 1% low como `1000 / média do 1% de dt mais lentos`, usando arredondamento para cima e pelo menos uma amostra; mediana e p95 devem usar metodologia explícita. Estes são contratos recomendados e precisam ser confrontados com a implementação.
5. Registrar duração, viewport, resolução efetiva, DPR, clima, número de carros e data. Invalidar ensaios interrompidos por aba oculta ou contexto perdido. Não inferir desempenho da RTX 3050 a partir de um navegador em nuvem.
6. Só relatar tempo de GPU com consulta suportada, resultado assíncrono válido e ausência de disjoint; não substituir esse dado por tempo de CPU. Heap JavaScript, contagens de recursos e VRAM são métricas diferentes.
7. Usar limiares diferentes, janelas sustentadas e intervalo mínimo entre alterações adaptativas. Suspender adaptação durante benchmark fixo. Reduzir resolução e recursos visuais sem alterar a frequência da física ou a capacidade dos adversários.
8. Começar com assets originais procedurais e registrar suas limitações visuais; migrar para modelos e texturas licenciados, LOD e compressão com evidência de ganho. Alta fidelidade e física profissional continuam etapas posteriores.

## Licenças e limites desta análise

O `LICENSE` do Black Hole é MIT, copyright 2026 Helio965. O bloco de noise em `shaders.js` declara Ashima Arts / Stefan Gustavson, MIT, com referência a [ashima/webgl-noise](https://github.com/ashima/webgl-noise). Qualquer cópia substancial deve preservar os avisos correspondentes. Nesta implementação, a referência serve como fonte de conceitos; não foram importados shaders, scripts de Windows, imagens ou assets dela.

Nenhum benchmark do Black Hole ou da RTX 3050 foi executado como parte desta análise. As observações acima distinguem comportamento lido no código, limitações identificadas e recomendações de evolução.
