# Benchmark gráfico reproduzível

A medição executa uma cena WebGL 2 fixa no circuito original Aurora Park v1, com câmera reiniciada e dez carros procedurais, pista, cenário e a condição selecionada (dia, noite ou chuva). Ela mede o fluxo completo de quadros apresentado pelo navegador, incluindo espera por sincronização; não é um teste isolado de potência da GPU.

1. Selecionar Baixa/Média/Alta, condição, meta 30/60/120 e duração 30/45/60 segundos.
2. Pré-compilar shaders com o renderizador. Aguardar três segundos de aquecimento fora da medição.
3. Registrar intervalos `requestAnimationFrame` sem truncar deltas, durante pelo menos 30 segundos. A física tem seu próprio timestep; não usa esses deltas diretamente.
4. Calcular FPS médio = 1000 / média dos tempos em ms; FPS mínimo = 1000 / maior tempo; 1% low = 1000 / média dos `ceil(N × 0,01)` tempos mais lentos.
5. Mediana usa índice inferior de 50%; p95 usa índice superior de 95% na amostra ordenada. Contar quadros acima de 50 ms como picos. “Estável” significa 1% low >= 80% do FPS médio; é uma heurística declarada.
6. Sugerir redução de um perfil quando 1% low < 80% da meta. Nenhum perfil superior é recomendado sem ser medido. Mesmo Baixa pode não atingir a meta: reduzir resolução ou meta e repetir.

A medição invalida ao ocultar a aba, redimensionar a janela, perder contexto WebGL ou observar uma pausa superior a dois segundos durante a medição. Durante o aquecimento, uma pausa assim reinicia os três segundos de estabilização; esses quadros nunca entram no relatório. Não se pode detectar todas as formas de throttling térmico ou do SO; opere em condições comparáveis. Nenhuma alteração adaptativa de perfil ocorre durante aquecimento e medição. Adaptação fora do teste: cinco segundos abaixo de 44 FPS, após período inicial de seis segundos; doze segundos entre reduções; nunca eleva o perfil automaticamente.

CPU é o tempo de submissão JavaScript da chamada de renderização, não o tempo total da CPU. Quando `EXT_disjoint_timer_query_webgl2` existe, uma consulta assíncrona pendente mede a GPU; amostras disjoint são descartadas. Na ausência da API ou em renderizadores por software, o campo permanece indisponível; uma consulta de software não é apresentada como tempo de GPU física. `performance.memory`, quando exposto, é heap JavaScript, não VRAM. O aplicativo não descobre a memória física de vídeo nem força uma GPU específica.

O relatório inclui amostras brutas, data UTC armazenada (interface em America/Sao_Paulo), identificação da cena, resolução real do drawing buffer, perfil, clima, renderizador e estatísticas. Histórico no IndexedDB e exportação JSON. Não comparar cenários, resoluções ou GPUs diferentes como se fossem iguais.

A RTX 3050 Laptop não está acessível na nuvem. O teste completo do navegador nesta máquina só demonstra execução da infraestrutura com o renderizador disponível, sem prever resultados no notebook.
