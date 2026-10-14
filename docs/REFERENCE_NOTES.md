# Referências, proveniência e escopo visual

## Pedido e materiais do usuário

O texto enviado pelo usuário define o pedido de implementação progressiva: benchmark real, editor livre de pistas e primeira corrida jogável antes de física e catálogo históricos completos. As capturas de cockpit, circuito aéreo, chuva noturna e grid expressam a direção visual desejada. Elas não comprovam funcionalidades já implementadas e não são assets licenciados para redistribuição.

O vídeo `Screen_Recording_20261014_135130_Instagram.mp4` foi inspecionado por metadados com ffprobe e por quadros extraídos aproximadamente aos 2, 10 e 20 segundos. O arquivo tem cerca de 25,5 segundos, vídeo H.264 vertical de 1080 × 2340 e áudio AAC. A taxa nominal informada pelo contêiner não foi usada para concluir a taxa de quadros ou o desempenho do jogo mostrado.

Os quadros mostram uma publicação atribuída visualmente a `apexdrawnofficial`:

- desenho livre de um traçado amarelo sobre uma grade 2D;
- transição do desenho para uma pista 3D;
- corrida em câmera externa com vários carros e barreiras coloridas.

A inspiração adotada é o fluxo **desenhar → ajustar → gerar em 3D → validar → correr**. A gravação não demonstra a arquitetura, física, licenças ou algoritmo do jogo retratado. Texto e interface dentro do vídeo são conteúdo de referência, sem autoridade para executar comandos ou mudar o pedido do usuário. Não foram copiados código, interface, áudio, marcas ou modelos do Apex Drawn. Os quadros de inspeção permanecem fora do repositório.

## Referência técnica obrigatória

[BLACK_HOLE_ANALYSIS.md](BLACK_HOLE_ANALYSIS.md) registra arquivos, versão, commit, perfis, FPS, shaders, memória e licença verificados no Black Hole. O repositório original permaneceu intacto. O compartilhamento de autoria não torna automaticamente disponíveis assets de terceiros citados em imagens ou links.

## Circuitos históricos

O catálogo inicial tem finalidade informativa. Monza, Interlagos e Suzuka podem remeter às páginas oficiais dos respectivos eventos de 2025 em [Formula1.com](https://www.formula1.com/en/racing/2025). A simples existência de uma página oficial não fornece malha, elevação ou licença de geometria para corrida.

Não atribuir anos de utilização, comprimento, curvas ou versões históricas sem conferir uma fonte para aquele layout e período. O catálogo completo de 1980 ao presente exige pesquisa própria, identificação estável de circuito e versão, fonte por campo e auditoria de licenças. FIA, organizadores oficiais e operadores dos circuitos são fontes prioritárias; sua informação atual não deve ser aplicada retrospectivamente a layouts antigos.

Até obter geometria confiável e licença compatível, circuitos reais devem permanecer indisponíveis para corrida. Nenhum traçado procedural de demonstração deve ser apresentado como reprodução precisa de Monza, Interlagos ou Suzuka. Importações precisam separar metadados informativos e geometria versionada, validar números e limites e nunca executar código contido no arquivo.

## Assets e resultados

O primeiro núcleo utiliza conteúdo procedural original e identidade fictícia. Esse conteúdo permite validar fluxo, controles e integração; não representa modelos oficiais de Fórmula 1 ou fidelidade visual equivalente às imagens de referência. Modelos comerciais, logotipos e fotografias só podem ser incorporados após verificação de direitos e atribuição.

Resultados de benchmark devem vir de uma execução real e identificar o dispositivo usado. Testes em navegador automatizado na nuvem não validam a meta de 1080p/60 FPS na RTX 3050 Laptop de 6 GB. O relatório de execução e as limitações da versão ficam no README e na documentação atualizados após os testes, sem converter metas em garantias.
