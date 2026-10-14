# Validação da primeira versão jogável

Executada no checkout `/workspace/F1`, com Node.js 24 e Chromium real usando WebGL 2 / SwiftShader. O renderizador é software; nenhum resultado desta máquina representa a RTX 3050 Laptop do usuário.

| Verificação | Resultado |
| --- | --- |
| Instalação reproduzível `npm ci` | Aprovada com o lockfile |
| `npm run lint` | Aprovado; TypeScript estrito e símbolos não utilizados |
| `npm test` | 22 testes aprovados em 3 arquivos |
| Build de produção | Aprovado, inclusive pelo servidor dos testes Playwright |
| Fluxo de corrida no Chromium | Aprovado: perfil persistido, homologação, aceleração por teclado, câmera, pausa, salvamento e viewport móvel sem overflow |
| Benchmark no Chromium | Aprovado: aquecimento separado, 30 segundos completos de medição, relatório com FPS positivo e histórico persistido após recarregar |
| Editor por mouse no Chromium | Aprovado: desenho de um laço novo, nome, validação, confirmação da homologação, salvamento e carregamento após recarregar |
| Auditoria npm | Nenhuma vulnerabilidade reportada no lockfile instalado |

Os três casos de navegador foram concluídos em invocações direcionadas: benchmark; condução e desenho. Não foram usados mocks de WebGL, resultados de FPS fabricados ou medições do dispositivo do usuário. Resultados que detectaram interrupção foram invalidados; não foram transformados em relatórios válidos. O limite de dois segundos continua ativo durante a medição. Pausas de compilação no aquecimento reiniciam apenas o período de estabilização.

O build emite um aviso de tamanho do chunk do Three.js (aproximadamente 500 kB minificado); isso não impede a compilação. A biblioteca fica em um chunk separado para cache.

Ainda não validados em hardware físico: RTX 3050 Laptop, gamepad e desempenho 1080p/60 FPS. Regras oficiais, física profissional, pit stops e catálogo histórico completo não são funcionalidades concluídas. Consulte o README e o método em BENCHMARK.md.

A CI remota tem seu próprio resultado no GitHub; o sucesso local não substitui esse status. GitHub Pages e o snapshot reutilizável do ambiente não foram publicados automaticamente.
