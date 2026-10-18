# Assets e licenças

- Carros de fórmula, asas, halo, suspensão visual, rodas, circuito, arquibancadas, boxes, vegetação, texturas de asfalto/grama/faixa e elementos da interface: geometria/desenho procedural original desta implementação, sob a licença MIT do projeto. Não são modelos oficiais nem reproduções exatas de veículos de fabricantes.
- **Pyodide 314.0.7** (CPython 3.14 compilado para WebAssembly), usado para executar o núcleo físico Python dentro de um Web Worker: Mozilla Public License 2.0, autores do Pyodide (https://github.com/pyodide/pyodide). O runtime (`pyodide.mjs`, `pyodide.asm.mjs`, `pyodide.asm.wasm`, `python_stdlib.zip`, `pyodide-lock.json`) é copiado sem modificação do pacote npm para `public/pyodide/` por `scripts/copy-pyodide.mjs` e servido pela mesma origem do jogo. A biblioteca padrão do CPython contida em `python_stdlib.zip` segue a licença da Python Software Foundation. Nenhum pacote Python de terceiros é carregado: o motor usa apenas a biblioteca padrão.
- Three.js 0.170.0, incluindo RoomEnvironment, PMREM e utilitários de geometria: licença MIT, autores do Three.js. Dependência fixada e aviso fornecido pelo pacote npm.
- **Barlow Condensed**: Copyright 2017 The Barlow Project Authors, https://github.com/jpt/barlow. SIL Open Font License 1.1. Pacote `@fontsource/barlow-condensed`; licença distribuída em `public/licenses/Barlow-Condensed-OFL.txt`.
- **DM Sans**: Copyright 2014 The DM Sans Project Authors, https://github.com/googlefonts/dm-fonts. SIL Open Font License 1.1. Pacote `@fontsource/dm-sans`; licença distribuída em `public/licenses/DM-Sans-OFL.txt`.
- Black Hole foi consultado como referência técnica MIT; não incorporamos seus shaders de buraco negro ou assets. Consulte a análise para avisos adicionais presentes no código de referência.
- Screenshots e vídeo enviados pelo usuário foram analisados como referência visual. Não são distribuídos com o projeto e não representam imagens deste jogo.

As fontes e o runtime Pyodide são empacotados localmente; o aplicativo não solicita fontes, imagens, modelos, código Python ou o runtime WebAssembly a CDNs durante a execução.
