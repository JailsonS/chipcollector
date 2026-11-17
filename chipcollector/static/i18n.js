/* ChipCollector — idiomas (PT/EN).
 *
 * A interface é escrita em português. Em inglês, cada texto da tela (nós de texto e os
 * atributos placeholder/title/aria-label) é trocado pela tradução abaixo, incluindo o que é
 * desenhado depois (MutationObserver). Textos com valores usam t("... {n} ...", {n}).
 * Mensagens do servidor passam por tr(), que também reconhece os modelos em SERVER_PATTERNS.
 * Para adicionar um idioma, inclua um dicionário em I18N e um botão em LANGS.
 */
"use strict";

const LANGS = [["pt", "PT"], ["en", "EN"]];

const I18N = { en: {
  // ---------------------------------------------------------------- geral / topo
  "Projetos": "Projects", "Usuários": "Users", "Senha": "Password", "Sair": "Log out",
  "Trocar senha": "Change password", "Nova senha (mín. 6 caracteres):": "New password (min. 6 characters):",
  "Senha alterada.": "Password changed.", "Sessão expirada.": "Session expired.",
  "Google Drive conectado.": "Google Drive connected.", "Idioma": "Language",
  "Salvar": "Save", "remover": "remove", "Nome": "Name", "E-mail": "Email", "Criar": "Create",
  "Carregando…": "Loading…",

  // ---------------------------------------------------------------- entrada
  "Amostras de referência para modelos de IA, coletadas em equipe.": "Reference samples for AI models, collected as a team.",
  "Gere chips de imagem a partir das suas camadas de referência, rotule ou desenhe o alvo direto no navegador e exporte um conjunto pronto para treinamento.":
    "Generate image chips from your reference layers, label or draw the target right in the browser, and export a dataset ready for training.",
  "01 / Carregar": "01 / Load", "02 / Anotar": "02 / Annotate", "03 / Exportar": "03 / Export",
  "Referências e imagens": "References and imagery",
  "Camadas vetoriais ou matriciais, Sentinel, Landsat, NICFI ou mosaicos próprios.": "Vector or raster layers, Sentinel, Landsat, NICFI or your own mosaics.",
  "Classificação ou segmentação": "Classification or segmentation",
  "Cada pessoa recebe os próprios chips, e cada rótulo guarda quem o fez.": "Each person gets their own chips, and every label records who made it.",
  "Pronto para treinar": "Ready to train",
  "GeoTIFF, máscaras e COCO, no seu computador ou no Google Drive.": "GeoTIFF, masks and COCO, on your computer or in Google Drive.",
  "Entrar": "Log in", "Criar conta": "Create account", "Já tenho conta": "I have an account",
  "Usuário": "Username", "Mostrar": "Show", "Ocultar": "Hide", "Mostrar senha": "Show password", "Ocultar senha": "Hide password",
  "Nome completo": "Full name", "Preencha usuário e senha.": "Enter a username and password.",
  "Ainda não há usuários. A primeira conta criada será a administradora.": "There are no users yet. The first account you create will be the administrator.",
  "Senha com pelo menos 6 caracteres.": "Password with at least 6 characters.",

  // ---------------------------------------------------------------- projetos
  "Escolha um projeto para anotar ou acompanhar.": "Pick a project to annotate or follow.",
  "+ Novo projeto": "+ New project", "Nenhum projeto ainda.": "No projects yet.",
  "segmentação": "segmentation", "classificação": "classification", "Abrir projeto →": "Open project →",
  "{a} de {b} chips completos": "{a} of {b} chips complete", "{n} anotações": "{n} annotations",
  "Projeto criado. Configure as bases de imagem e as camadas de referência.": "Project created. Set up the imagery and the reference layers.",
  "Alvo de detecção": "Detection target", "ex.: extração madeireira": "e.g. selective logging", "Descrição": "Description",
  "Tipo de anotação": "Annotation type", "Classificação (rótulo por chip)": "Classification (one label per chip)",
  "Segmentação (polígonos)": "Segmentation (polygons)", "Classes por chip": "Classes per chip",
  "Uma classe por chip": "One class per chip", "Múltiplas classes por chip": "Several classes per chip",
  "Tamanho do chip (pixels)": "Chip size (pixels)", "Resolução (m/pixel)": "Resolution (m/pixel)",
  "Anotadores por chip": "Annotators per chip", "Classes": "Classes",
  "Classificação binária: use uma única classe (presença/ausência). O id é o valor gravado nas máscaras.":
    "Binary classification: use a single class (presence/absence). The id is the value written to the masks.",
  "Id": "Id", "Cor": "Color", "+ classe": "+ class", "Criar projeto": "Create project", "alvo": "target",

  // ---------------------------------------------------------------- abas
  "Anotar": "Annotate", "Mapa": "Map", "Configurar": "Set up", "Exportar": "Export", "Progresso": "Progress",

  // ---------------------------------------------------------------- anotação
  "Este projeto ainda não tem bases de imagem.": "This project has no imagery yet.",
  "Peça a um administrador para configurar.": "Ask an administrator to set it up.",
  "✎ Polígono": "✎ Polygon", "▭ Retângulo": "▭ Rectangle", "⬚ Editar": "⬚ Edit", "✂ Recortar": "✂ Cut",
  "🗑 Apagar": "🗑 Delete", "⇩ Usar referência": "⇩ Use reference", "Limpar": "Clear", "Referência": "Reference", "Outros": "Others",
  "Desenhar polígono (P)": "Draw polygon (P)", "Retângulo": "Rectangle", "Editar vértices (E)": "Edit vertices (E)",
  "Recortar polígonos": "Cut polygons", "Apagar polígono (Del)": "Delete polygon (Del)",
  "Copiar polígonos de referência para edição": "Copy reference polygons for editing",
  "Remover todos os polígonos": "Remove all polygons", "Mostrar/ocultar referência (R)": "Show/hide reference (R)",
  "Anotações de outros usuários": "Other users' annotations", "Enquadrar": "Fit to chip",
  "Filtrar por estrato": "Filter by stratum", "todos os estratos": "all strata",
  "Base de imagem": "Imagery", "Mín.": "Min.", "Máx.": "Max.", "Comparar com": "Compare with",
  "Classe para desenhar": "Class to draw", "Rótulo do chip": "Chip label", "Comentário": "Comment", "opcional": "optional",
  "Salvar e próximo (Enter)": "Save and next (Enter)", "Incerto": "Unsure", "Salvar como incerto (U)": "Save as unsure (U)",
  "Pular": "Skip", "Pular — imagem ruim/nuvem (S)": "Skip — bad image/clouds (S)", "Minhas últimas": "My latest",
  "Sem polígonos = ausência do alvo. Clique num polígono para trocar sua classe para a selecionada.":
    "No polygons = target absent. Click a polygon to change it to the selected class.",
  "Presente: {name}": "Present: {name}", "Ausente": "Absent", "Nenhuma (ausência)": "None (absent)",
  "Não há mais chips disponíveis para você neste projeto/estrato. 🎉": "No more chips available for you in this project/stratum. 🎉",
  "Fila vazia": "Queue empty", "Chip de outro projeto.": "Chip belongs to another project.",
  "estrato {s}": "stratum {s}", "{a}/{b} anotações": "{a}/{b} annotations", "já anotado por você ({s})": "already annotated by you ({s})",
  "concluída": "done", "incerta": "unsure", "pulada": "skipped",
  "Buscando imagem… (a primeira leitura de cada base pode levar alguns segundos)": "Fetching imagery… (the first read of each source can take a few seconds)",
  "Erro ao obter imagem": "Could not load the image", "cenas: {d}": "scenes: {d}", "pixels válidos: {p}%": "valid pixels: {p}%",
  "Remover todos os polígonos deste chip?": "Remove all polygons from this chip?",
  "Polígono → {name}": "Polygon → {name}", "Sem polígonos de referência neste chip.": "No reference polygons in this chip.",
  "{n} polígono(s) copiados — ajuste os vértices com \"Editar\".": "{n} polygon(s) copied — adjust the vertices with \"Edit\".",
  "Escolha um rótulo (presença/ausência ou classe) antes de salvar.": "Pick a label (presence/absence or a class) before saving.",
  "Chip pulado.": "Chip skipped.", "Salvo ✓": "Saved ✓", "nenhuma ainda": "none yet", "ausente": "absent",

  // ---------------------------------------------------------------- mapa
  "completo": "complete", "parcial": "partial", "pendente": "pending", "anotado por mim": "annotated by me",
  "Camada p/ chip manual": "Layer for manual chips", "(nenhuma)": "(none)", "+ Chip por clique": "+ Chip by click",
  "#{id} · {s} · {n} anot.": "#{id} · {s} · {n} annot.", "Chip #{id} criado.": "Chip #{id} created.",

  // ---------------------------------------------------------------- configuração
  "Parâmetros do projeto": "Project settings", "Bases de imagem (sensores e períodos)": "Imagery (sensors and periods)",
  "Camadas de referência": "Reference layers", "Gerar chips (amostragem estratificada)": "Generate chips (stratified sampling)",
  "Rasters locais (opcional)": "Local rasters (optional)", "Zona de perigo": "Danger zone", "Excluir projeto": "Delete project",
  "As imagens de cada chip são buscadas sob demanda e reamostradas para a grade do chip ({size}px × {res} m). Planetary Computer e Earth Search são gratuitos e não exigem conta; o Google Earth Engine é gratuito para ONGs/pesquisa (exige projeto Cloud registrado — {gee}).":
    "Each chip's imagery is fetched on demand and resampled to the chip grid ({size}px × {res} m). Planetary Computer and Earth Search are free and need no account; Google Earth Engine is free for NGOs/research (requires a registered Cloud project — {gee}).",
  "configurado ✓": "configured ✓", "não configurado no servidor": "not configured on the server",
  "+ base de imagem": "+ imagery source", "Salvar bases": "Save imagery", "Nenhuma base configurada.": "No imagery configured.",
  "Vetor (GPKG, GeoJSON, SHP zipado, KML, FGB, Parquet) com um campo de classe para estratificar, ou raster categórico (GeoTIFF, ex.: MapBiomas, PRODES).":
    "Vector (GPKG, GeoJSON, zipped SHP, KML, FGB, Parquet) with a class field to stratify by, or a categorical raster (GeoTIFF, e.g. MapBiomas, PRODES).",
  "Arquivo(s)": "File(s)", "Tema": "Theme", "desmatamento, LULC…": "deforestation, LULC…", "Campo de classe (vetor)": "Class field (vector)",
  "ex.: classe": "e.g. class", "Enviar camada": "Upload layer", "Camada": "Layer", "Estratos": "Strata",
  "{theme} · {kind} · {n} feições": "{theme} · {kind} · {n} features", "· campo {f}": "· field {f}",
  "Envie mosaicos próprios (GeoTIFF/COG — ex.: Planet NICFI baixado, CBERS, drone) para usar como base de imagem.":
    "Upload your own mosaics (GeoTIFF/COG — e.g. downloaded Planet NICFI, CBERS, drone) to use as imagery.",
  "Enviar raster": "Upload raster", "{n} bandas ({names}) · {crs}": "{n} bands ({names}) · {crs}",
  "Chips por estrato": "Chips per stratum", "Chips de fundo (fora das feições)": "Background chips (outside features)",
  "Espaçamento mín. (px)": "Min. spacing (px)", "{n} (sem sobreposição)": "{n} (no overlap)", "Posição": "Placement",
  "aleatória dentro da feição": "random inside the feature", "centro da feição": "feature centre", "Semente": "Seed",
  "Gerar chips": "Generate chips", "Gerando…": "Generating…", "Chips atuais: {n}": "Current chips: {n}",
  "Apagar chips sem anotação": "Delete unannotated chips", "Projeto salvo.": "Project saved.", "Bases salvas.": "Imagery saved.",
  "Selecione o(s) arquivo(s).": "Select the file(s).", "Camada enviada: {n} feições. Campos: {f}": "Layer uploaded: {n} features. Fields: {f}",
  "Remover camada?": "Remove layer?", "Selecione um GeoTIFF.": "Select a GeoTIFF.", "Raster enviado.": "Raster uploaded.",
  "Remover raster?": "Remove raster?", "Envie uma camada de referência primeiro.": "Upload a reference layer first.",
  "Usar": "Use", "Estrato": "Stratum", "Feições/pixels": "Features/pixels", "Classe do projeto (pré-rotulagem)": "Project class (pre-labelling)",
  "{n} chips criados.": "{n} chips created.", "Apagar todos os chips ainda não anotados?": "Delete all chips that have no annotations yet?",
  "{n} chips removidos.": "{n} chips removed.", "Digite o nome do projeto para excluir: {name}": "Type the project name to delete it: {name}",
  "Rótulo": "Label", "Provedor": "Provider", "Raster": "Raster", "Sensor": "Sensor", "Início": "Start", "Fim": "End",
  "Nuvens máx. (%)": "Max. clouds (%)", "Composição": "Composite", "mediana": "median", "cena menos nublada": "least cloudy scene",
  "Bandas exportadas": "Exported bands", "padrão do sensor": "sensor default", "RGB": "RGB", "padrão": "default",
  "Contraste (mín,máx)": "Stretch (min,max)", "auto 2–98%": "auto 2–98%", "Coleção GEE (custom/NICFI)": "GEE collection (custom/NICFI)",
  // rótulos do catálogo vindos do servidor
  "Landsat 8/9 OLI Collection 2 L2 (30 m)": "Landsat 8/9 OLI Collection 2 L2 (30 m)",
  "Landsat 5/7 TM/ETM+ Collection 2 L2 (30 m) — séries históricas": "Landsat 5/7 TM/ETM+ Collection 2 L2 (30 m) — historical series",
  "Planet NICFI mosaicos trópicos (4,77 m, mensal) — somente GEE": "Planet NICFI tropical mosaics (4.77 m, monthly) — GEE only",
  "Coleção personalizada (GEE)": "Custom collection (GEE)", "Raster local enviado pelo usuário": "Local raster uploaded by the user",
  "Raster local": "Local raster", "Microsoft Planetary Computer (STAC, gratuito)": "Microsoft Planetary Computer (STAC, free)",
  "Element84 Earth Search / AWS Open Data (STAC, gratuito)": "Element84 Earth Search / AWS Open Data (STAC, free)",
  "Google Earth Engine (gratuito p/ ONGs, pesquisa e uso não comercial)": "Google Earth Engine (free for NGOs, research and non-commercial use)",
  "Raster local (GeoTIFF/COG enviado)": "Local raster (uploaded GeoTIFF/COG)",

  // ---------------------------------------------------------------- exportação
  "Exportar amostras": "Export samples", "Bases de imagem a incluir": "Imagery to include", "Status": "Status",
  "concluídas": "done", "incertas": "unsure", "incluir imagens (GeoTIFF por chip)": "include imagery (GeoTIFF per chip)",
  "Treino": "Train", "Validação": "Validation", "Teste": "Test", "Destino": "Destination",
  "O arquivo .zip fica sempre disponível para download (salvar no computador).": "The .zip file is always available to download (save to your computer).",
  "Google Drive conectado": "Google Drive connected", "desconectar": "disconnect", "Conectar meu Google Drive": "Connect my Google Drive",
  "Drive via conta de serviço": "Drive via service account",
  "Google Drive não configurado no servidor (veja README).": "Google Drive is not configured on the server (see README).",
  "enviar também para o Google Drive (pasta \"ChipCollector\")": "also upload to Google Drive (\"ChipCollector\" folder)",
  "Gerar pacote": "Build package",
  "Conteúdo: GeoTIFFs multibanda, máscaras de segmentação (GeoTIFF uint8), COCO JSON, labels.csv, GeoJSON das anotações e dos chips, divisão treino/val/teste e metadados com autoria.":
    "Contents: multiband GeoTIFFs, segmentation masks (uint8 GeoTIFF), COCO JSON, labels.csv, GeoJSON of annotations and chips, train/val/test split and metadata with authorship.",
  "Exportações": "Exports", "abrir no Drive": "open in Drive", "Nenhuma exportação.": "No exports yet.",
  "Exportação iniciada.": "Export started.", "pending": "pending", "running": "running",

  // ---------------------------------------------------------------- progresso / usuários
  "Chips": "Chips", "Chips completos": "Complete chips", "Anotações": "Annotations", "Anotadores": "Annotators",
  "Por anotador": "By annotator", "Concluídas": "Done", "Incertas": "Unsure", "Puladas": "Skipped",
  "Por rótulo": "By label", "Chips por estrato": "Chips per stratum", "(ausente)": "(absent)",
  "Admin": "Admin", "Ativo": "Active", "Novo usuário": "New user", "Senha inicial": "Initial password",
  "administrador": "administrator", "Usuário criado.": "User created.",

  // ---------------------------------------------------------------- mensagens do servidor
  "Concluído": "Done", "Rótulos e máscaras": "Labels and masks", "Gravando arquivos": "Writing files",
  "Compactando": "Compressing", "Enviando ao Google Drive": "Uploading to Google Drive",
  "Arquivo zip com caminho inválido.": "Zip file with an invalid path.",
  "Fonte local sem raster associado.": "Local source has no raster attached.",
  "Google Drive não conectado. Conecte sua conta em 'Exportar' ou configure uma conta de serviço.": "Google Drive is not connected. Connect your account under 'Export' or set up a service account.",
  "Nenhum arquivo geoespacial reconhecido (use GPKG, GeoJSON, SHP, KML, FGB, Parquet ou GeoTIFF).": "No recognised geospatial file (use GPKG, GeoJSON, SHP, KML, FGB, Parquet or GeoTIFF).",
  "Nenhuma cena encontrada no GEE para o período/nuvens escolhidos.": "No GEE scenes found for the chosen period/cloud limit.",
  "Nenhuma cena encontrada para o período/nuvens escolhidos. Amplie o período ou o limite de nuvens.": "No scenes found for the chosen period/cloud limit. Widen the period or the cloud limit.",
  "Não há anotações com os status escolhidos para exportar.": "There are no annotations with the chosen status to export.",
  "Raster com mais de 512 valores distintos — use um raster categórico (classes inteiras).": "Raster has more than 512 distinct values — use a categorical raster (integer classes).",
  "Raster de referência deve ser categórico (valores inteiros por classe).": "The reference raster must be categorical (integer values per class).",
  "Informe 'collection' (ID da ImageCollection no GEE) para sensor custom.": "Enter 'collection' (GEE ImageCollection ID) for the custom sensor.",
  "Conecte o Google Drive antes (ou configure conta de serviço).": "Connect Google Drive first (or set up a service account).",
  "Google Drive não configurado no servidor.": "Google Drive is not configured on the server.",
  "Defina pelo menos uma classe.": "Define at least one class.", "Envie um GeoTIFF/COG.": "Upload a GeoTIFF/COG.",
  "Estado OAuth inválido.": "Invalid OAuth state.", "Este projeto permite apenas uma classe por chip.": "This project allows only one class per chip.",
  "O projeto não tem nenhuma base de imagem configurada.": "The project has no imagery configured.",
  "OAuth do Google Drive não configurado (CHIPS_DRIVE_CLIENT_SECRETS).": "Google Drive OAuth is not configured (CHIPS_DRIVE_CLIENT_SECRETS).",
  "Raster local da fonte não encontrado.": "The source's local raster was not found.", "Rótulo inválido para o projeto.": "Invalid label for this project.",
  "Senha muito curta.": "Password too short.", "Todo polígono precisa de uma classe válida do projeto.": "Every polygon needs a valid project class.",
  "Usuário obrigatório e senha com pelo menos 6 caracteres.": "Username required and a password with at least 6 characters.",
  "Não autenticado.": "Not logged in.", "Usuário ou senha inválidos.": "Wrong username or password.",
  "Apenas administradores.": "Administrators only.", "Cadastro desabilitado. Peça a um administrador.": "Sign-up is disabled. Ask an administrator.",
  "Sem permissão.": "Not allowed.", "Arquivo não disponível.": "File not available.", "Camada não encontrada no projeto.": "Layer not found in this project.",
  "Camada não encontrada.": "Layer not found.", "Chip não encontrado.": "Chip not found.", "Exportação não encontrada.": "Export not found.",
  "Projeto não encontrado.": "Project not found.", "Raster não encontrado.": "Raster not found.", "Usuário não encontrado.": "User not found.",
  "Nome de usuário já existe.": "That username is taken.",
  "Tamanho do chip/resolução não podem mudar depois que chips foram gerados.": "Chip size/resolution cannot change after chips have been generated.",
}};

// Mensagens do servidor com valores: [regex PT, modelo EN com $1, $2…]
const SERVER_PATTERNS = { en: [
  [/^Imagens (\d+)\/(\d+)$/, "Imagery $1/$2"],
  [/^Banda '(.*)' não encontrada no raster \((.*)\)\.$/, "Band '$1' not found in the raster ($2)."],
  [/^Campo '(.*)' não existe\. Campos: (.*)$/, "Field '$1' does not exist. Fields: $2"],
  [/^Erro do Earth Engine: (.*)$/s, "Earth Engine error: $1"],
  [/^Falha ao inicializar o Earth Engine: (.*)$/s, "Could not initialise Earth Engine: $1"],
  [/^Imagem com dimensão inesperada (.*)$/, "Image with unexpected size $1"],
  [/^Provedor desconhecido: (.*)$/, "Unknown provider: $1"],
  [/^Sensor '(.*)' não disponível no provedor '(.*)'\.$/, "Sensor '$1' is not available from provider '$2'."],
  [/^Sensor (.*) não suportado no GEE\.$/, "Sensor $1 is not supported in GEE."],
  [/^Sensor (.*) indisponível em (.*)\.$/, "Sensor $1 is not available in $2."],
  [/^Fonte '(.*)': informe data inicial e final\.$/, "Source '$1': enter a start and end date."],
  [/^Id de fonte repetido: (.*)$/, "Duplicate source id: $1"],
  [/^Fonte de imagem '(.*)' não encontrada\.$/, "Imagery source '$1' not found."],
  [/^Erro interno \((.*)\)\. Veja o log do servidor\.$/, "Internal error ($1). See the server log."],
]};

const norm = (s) => String(s).replace(/\s+/g, " ").trim();
for (const d of Object.values(I18N)) for (const k of Object.keys(d)) { const n = norm(k); if (n !== k) { d[n] = d[k]; delete d[k]; } }

let LANG = (() => {
  try { const v = localStorage.getItem("chipcollector.lang"); if (v) return v; } catch (_) {}
  return (navigator.language || "pt").toLowerCase().startsWith("pt") ? "pt" : "en";
})();
if (!LANGS.some(([k]) => k === LANG)) LANG = "pt";

/** Texto com valores: t("{n} chips criados.", {n: 3}) */
function t(s, vars) {
  let out = (I18N[LANG] && I18N[LANG][norm(s)]) || s;
  if (vars) for (const [k, v] of Object.entries(vars)) out = out.split(`{${k}}`).join(String(v));
  return out;
}
/** Mensagem vinda do servidor (texto fixo ou modelo conhecido). */
function tr(s) {
  if (typeof s !== "string" || LANG === "pt") return s;
  const d = I18N[LANG] || {};
  if (d[norm(s)]) return d[norm(s)];
  for (const [re, rep] of SERVER_PATTERNS[LANG] || []) if (re.test(s)) return s.replace(re, rep);
  return s;
}

const ATTRS = ["placeholder", "title", "aria-label"];
function translateTree(root) {
  if (LANG === "pt") return;
  const d = I18N[LANG];
  const swap = (node) => {
    const raw = node.nodeValue, key = norm(raw);
    if (!key || !d[key] || d[key] === key) return;
    const lead = raw.match(/^\s*/)[0], trail = raw.match(/\s*$/)[0];
    node.nodeValue = lead + d[key] + trail;
  };
  const attrs = (el) => { for (const a of ATTRS) { const v = el.getAttribute && el.getAttribute(a); if (v && d[norm(v)] && d[norm(v)] !== v) el.setAttribute(a, d[norm(v)]); } };
  if (root.nodeType === Node.TEXT_NODE) return swap(root);
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  if (root.closest && root.closest("[translate=no]")) return;
  attrs(root);
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
    acceptNode: (n) => (n.nodeType === Node.ELEMENT_NODE && (n.getAttribute("translate") === "no" || n.tagName === "SCRIPT" || n.tagName === "STYLE"))
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  let n;
  while ((n = w.nextNode())) n.nodeType === Node.TEXT_NODE ? swap(n) : attrs(n);
}

const i18nObserver = new MutationObserver((muts) => {
  for (const m of muts) {
    if (m.type === "attributes") translateTree(m.target);
    else m.addedNodes.forEach(translateTree);
  }
});

function setLang(lang) {
  LANG = lang;
  try { localStorage.setItem("chipcollector.lang", lang); } catch (_) {}
  document.documentElement.lang = lang === "pt" ? "pt-BR" : lang;
  renderLangSwitch();
}

function renderLangSwitch() {
  const box = document.getElementById("langbox");
  if (!box) return;
  box.innerHTML = LANGS.map(([k, label]) => `<button type="button" data-lang="${k}" class="${k === LANG ? "on" : ""}" aria-pressed="${k === LANG}">${label}</button>`).join("");
  box.querySelectorAll("button").forEach((b) => (b.onclick = () => {
    if (b.dataset.lang === LANG) return;
    setLang(b.dataset.lang);
    if (typeof onLangChange === "function") onLangChange();
  }));
}

// o script é carregado no fim do <body>, então o DOM já existe
setLang(LANG);
i18nObserver.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ATTRS });
translateTree(document.body);
