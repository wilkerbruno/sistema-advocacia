/**
 * Tutorial guiado de primeiro acesso.
 *
 * Passeio curto pelo Painel e pelo menu lateral, mostrado sozinho na
 * primeira vez que um usuário chega no Painel (ver `tour_deve_iniciar` em
 * app/__init__.py::injetar_globais e Usuario.tour_concluido_em). Também
 * pode ser revisto a qualquer momento pelo link "Rever tutorial" (Minha
 * conta), que usa `?tutorial=1` na URL do Painel.
 *
 * Decisões de propósito:
 *  - Sem biblioteca nenhuma (mesmo espírito de não usar biblioteca de
 *    calendário na Agenda) — é um efeito simples (destacar um elemento +
 *    um cartãozinho de texto ao lado) e uma dependência nova só pra isso
 *    não compensa.
 *  - Cada passo aponta pra um elemento real da tela via atributo
 *    `data-tour="..."` (ver app/templates/base.html e
 *    app/templates/dashboard/index.html). Se o elemento não existir nesta
 *    conta (ex.: autenticador desligado no servidor, ou um item que só
 *    aparece por papel/módulo), o passo é pulado sozinho — nunca trava o
 *    tour numa tela em branco.
 *  - Roda inteiramente na própria página do Painel (nunca navega pra
 *    outra URL no meio do tour) — mais simples e mais confiável do que
 *    tentar retomar um tour "no meio" depois de trocar de página.
 */
(function () {
  if (!window.TOUR_AUTO_INICIAR && location.search.indexOf("tutorial=1") === -1) {
    return; // nada a fazer nesta página/visita
  }

  // Cada passo: `titulo`/`texto` (conteúdo do cartão), `selector` (alvo a
  // destacar, ou null pra um cartão centralizado sem destacar nada) e,
  // quando o alvo mora dentro de um grupo recolhível do menu, `grupo`
  // (o data-grupo daquele grupo — garante que ele esteja aberto antes de
  // tentar destacar um item de dentro dele).
  var PASSOS = [
    {
      titulo: "Bem-vindo(a) ao JusControl!",
      texto: "Vamos fazer um tour rápido pelas principais áreas do sistema — leva menos de dois minutos. " +
        "Você pode pular a qualquer momento e rever depois em Configurações > Minha conta > Rever tutorial.",
      selector: null,
    },
    {
      titulo: "Seu Painel",
      texto: "É a primeira tela ao entrar no sistema: um resumo rápido de processos ativos, clientes, " +
        "prazos em atenção e prazos perdidos, todos clicáveis.",
      selector: '[data-tour="kpi-painel"]',
    },
    {
      titulo: "Operação",
      texto: "Aqui fica o trabalho do dia a dia do escritório: Processos, Clientes (com o funil de " +
        "captação), Rotina (Tarefas, Agenda e Horas), Agente de IA e, quando você tiver acesso, Financeiro.",
      selector: '[data-tour="grupo-operacao"] .grupo-toggle',
    },
    {
      titulo: "Processos",
      texto: "Cadastre e acompanhe processos: prazos (com a data de segurança), audiências, documentos " +
        "e as ferramentas de IA jurídica de cada caso.",
      selector: '[data-tour="menu-processos"]',
      grupo: "operacao",
    },
    {
      titulo: "Clientes",
      texto: "Cadastro de clientes, com os dados de LGPD e o alerta automático de conflito de interesse.",
      selector: '[data-tour="menu-clientes"]',
      grupo: "operacao",
    },
    {
      titulo: "Rotina",
      texto: "Tarefas, Agenda (um calendário único reunindo prazos, audiências, tarefas com vencimento e " +
        "compromissos) e Horas — tudo agrupado numa tela só, em abas.",
      selector: '[data-tour="menu-rotina"]',
      grupo: "operacao",
    },
    {
      titulo: "Governança de carteira",
      texto: "Se o seu escritório contratou este módulo, aqui você tem uma visão consolidada de toda a " +
        "carteira: prazos críticos, métricas, produtividade e mais.",
      selector: '[data-tour="grupo-governanca"] .grupo-toggle',
    },
    {
      titulo: "Configurações",
      texto: "Sua conta, a equipe do escritório e as configurações administrativas ficam aqui.",
      selector: '[data-tour="grupo-config"] .grupo-toggle',
    },
    {
      titulo: "Minha conta",
      texto: "Suas preferências pessoais: categoria favorita do menu, autenticador (2FA — por segurança, " +
        "o login pede um código de 6 dígitos gerado por um app no celular) e o seu agente local.",
      selector: '[data-tour="menu-minha-conta"]',
      grupo: "config",
    },
    {
      titulo: "Pronto!",
      texto: "Você já conhece o caminho das pedras. Pode rever este tutorial quando quiser em " +
        "Configurações > Minha conta > Rever tutorial.",
      selector: null,
    },
  ];

  var passoAtual = 0;
  var elBackdrop, elSpot, elCartao;
  var ativo = false;

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  function montarDom() {
    elBackdrop = document.createElement("div");
    elBackdrop.className = "tour-backdrop";
    elSpot = document.createElement("div");
    elSpot.className = "tour-spot";
    elCartao = document.createElement("div");
    elCartao.className = "tour-cartao";
    elCartao.setAttribute("role", "dialog");
    elCartao.setAttribute("aria-live", "polite");
    document.body.appendChild(elBackdrop);
    document.body.appendChild(elSpot);
    document.body.appendChild(elCartao);

    elBackdrop.addEventListener("click", pular);
    document.addEventListener("keydown", aoTeclar);
    window.addEventListener("resize", reposicionar);
    window.addEventListener("scroll", reposicionar, true);
  }

  function desmontarDom() {
    [elBackdrop, elSpot, elCartao].forEach(function (el) {
      if (el && el.parentNode) el.parentNode.removeChild(el);
    });
    document.removeEventListener("keydown", aoTeclar);
    window.removeEventListener("resize", reposicionar);
    window.removeEventListener("scroll", reposicionar, true);
  }

  function aoTeclar(ev) {
    if (ev.key === "Escape") pular();
    if (ev.key === "ArrowRight") avancar();
    if (ev.key === "ArrowLeft") voltar();
  }

  // Garante que o grupo recolhível do menu (Operação/Governança/Config)
  // esteja aberto antes de tentar destacar um item de dentro dele —
  // reaproveita o mesmo botão/clique que o usuário usaria manualmente
  // (ver o toggle já registrado em base.html), então também respeita e
  // atualiza a preferência salva em localStorage normalmente.
  function garantirGrupoAberto(nomeGrupo) {
    if (!nomeGrupo) return;
    var grupoEl = document.querySelector('.grupo-colapsavel[data-grupo="' + nomeGrupo + '"]');
    if (!grupoEl) return;
    var itens = grupoEl.querySelector(".grupo-itens");
    var toggle = grupoEl.querySelector(".grupo-toggle");
    if (itens && toggle && itens.classList.contains("recolhido")) {
      toggle.click();
    }
  }

  function resolverAlvo(passo) {
    if (!passo.selector) return null;
    garantirGrupoAberto(passo.grupo);
    var el = document.querySelector(passo.selector);
    if (!el || el.offsetParent === null) return null;
    return el;
  }

  function posicionarSpot(elAlvo) {
    if (!elAlvo) {
      var cx = window.innerWidth / 2, cy = window.innerHeight / 2;
      elSpot.style.top = cy + "px";
      elSpot.style.left = cx + "px";
      elSpot.style.width = "0px";
      elSpot.style.height = "0px";
      return;
    }
    var r = elAlvo.getBoundingClientRect();
    var pad = 8;
    elSpot.style.top = (r.top - pad) + "px";
    elSpot.style.left = (r.left - pad) + "px";
    elSpot.style.width = (r.width + pad * 2) + "px";
    elSpot.style.height = (r.height + pad * 2) + "px";
  }

  function posicionarCartao(elAlvo) {
    var W = window.innerWidth, H = window.innerHeight;
    var cw = elCartao.offsetWidth, ch = elCartao.offsetHeight;
    var top, left;
    if (elAlvo) {
      var r = elAlvo.getBoundingClientRect();
      left = r.right + 20;
      top = r.top;
      if (left + cw > W - 16) left = r.left - cw - 20;
      if (left < 16 || left + cw > W - 16) {
        left = Math.min(Math.max(r.left, 16), W - cw - 16);
        top = r.bottom + 20;
      }
    } else {
      left = (W - cw) / 2;
      top = (H - ch) / 2;
    }
    top = Math.min(Math.max(top, 16), H - ch - 16);
    left = Math.min(Math.max(left, 16), W - cw - 16);
    elCartao.style.top = top + "px";
    elCartao.style.left = left + "px";
  }

  var alvoAtualParaReposicionar = null;

  function reposicionar() {
    if (!ativo) return;
    posicionarSpot(alvoAtualParaReposicionar);
    posicionarCartao(alvoAtualParaReposicionar);
  }

  function renderizarPasso(passo, elAlvo) {
    alvoAtualParaReposicionar = elAlvo;
    var ultimo = passoAtual === PASSOS.length - 1;
    elCartao.innerHTML =
      '<div class="tour-cartao-passos">Passo ' + (passoAtual + 1) + " de " + PASSOS.length + "</div>" +
      "<h4>" + escaparHtml(passo.titulo) + "</h4>" +
      "<p>" + escaparHtml(passo.texto) + "</p>" +
      '<div class="tour-cartao-acoes">' +
      '<button type="button" class="tour-pular" data-acao="pular">Pular tutorial</button>' +
      '<div class="tour-cartao-acoes-dir">' +
      (passoAtual > 0 ? '<button type="button" class="btn btn-outline-doc btn-sm" data-acao="voltar">Voltar</button>' : "") +
      '<button type="button" class="btn btn-ink btn-sm" data-acao="avancar">' + (ultimo ? "Concluir" : "Próximo") + "</button>" +
      "</div></div>";

    elCartao.querySelector('[data-acao="pular"]').addEventListener("click", pular);
    elCartao.querySelector('[data-acao="avancar"]').addEventListener("click", avancar);
    var btnVoltar = elCartao.querySelector('[data-acao="voltar"]');
    if (btnVoltar) btnVoltar.addEventListener("click", voltar);

    if (elAlvo) elAlvo.scrollIntoView({ block: "nearest" });
    posicionarSpot(elAlvo);
    posicionarCartao(elAlvo);
  }

  function escaparHtml(texto) {
    var div = document.createElement("div");
    div.textContent = texto == null ? "" : String(texto);
    return div.innerHTML;
  }

  function mostrarPasso(indice, direcao) {
    direcao = direcao || 1;
    if (indice < 0) { finalizar(); return; }
    if (indice >= PASSOS.length) { finalizar(); return; }
    var passo = PASSOS[indice];
    var el = resolverAlvo(passo);
    if (passo.selector && !el) {
      // Alvo não existe nesta conta (papel/módulo não tem aquele item,
      // ou o autenticador está desligado no servidor) — pula sozinho.
      mostrarPasso(indice + direcao, direcao);
      return;
    }
    passoAtual = indice;
    renderizarPasso(passo, el);
  }

  function avancar() { mostrarPasso(passoAtual + 1, 1); }
  function voltar() { mostrarPasso(passoAtual - 1, -1); }

  function pular() { finalizar(); }

  function finalizar() {
    if (!ativo) return;
    ativo = false;
    desmontarDom();

    // Conta tanto concluir quanto pular como "já viu" — ver docstring da
    // rota em app/routes/conta.py::tutorial_concluir.
    fetch(window.TOUR_URL_CONCLUIR, {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken() },
    }).catch(function () { /* falha silenciosa: pior caso, oferece de novo no próximo login */ });

    // Some com ?tutorial=1 da URL (usado por "Rever tutorial"), pra um F5
    // simples não reabrir o tour sozinho de novo.
    if (window.history && window.history.replaceState && location.search.indexOf("tutorial=1") !== -1) {
      var url = new URL(location.href);
      url.searchParams.delete("tutorial");
      var novaQuery = url.searchParams.toString();
      window.history.replaceState(null, "", url.pathname + (novaQuery ? "?" + novaQuery : "") + url.hash);
    }
  }

  function iniciar() {
    ativo = true;
    montarDom();
    mostrarPasso(0, 1);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }
})();
