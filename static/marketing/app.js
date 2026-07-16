/* afterhours marketing demos */
(function () {
  const demos = {
    quiz: {
      title: "Biology Notes (Week 4) · Quiz",
      items: [
        { q: "What is the powerhouse of the cell?", type: "text", answer: "mitochondria" },
        { q: "What molecule carries genetic information?", type: "text", answer: "dna" },
        { q: "Name the process plants use to make sugar from light.", type: "text", answer: "photosynthesis" },
      ],
    },
    practise: {
      title: "Geography Lecture Notes (Week 2) · Practise",
      items: [
        {
          q: "Which is the largest ocean on Earth?",
          type: "mcq",
          options: ["Pacific Ocean", "Atlantic Ocean", "Indian Ocean", "Arctic Ocean"],
          answer: 0,
        },
        {
          q: "Which continent is also a country?",
          type: "mcq",
          options: ["Australia", "Europe", "Asia", "Africa"],
          answer: 0,
        },
        {
          q: "What is the capital of New Zealand?",
          type: "mcq",
          options: ["Auckland", "Wellington", "Christchurch", "Hamilton"],
          answer: 1,
        },
      ],
    },
    slideshow: {
      title: "History Lecture Notes (Week 3) · Slideshow",
      items: [
        { q: "In which year did World War II end?", a: "1945" },
        { q: "Who was the first Prime Minister of New Zealand?", a: "Henry Sewell" },
        { q: "What year did the Treaty of Waitangi take place?", a: "1840" },
      ],
    },
  };

  const stage = document.getElementById("demo-stage");
  const tabs = Array.from(document.querySelectorAll(".mode-tabs [data-demo]"));
  let mode = "quiz";
  let index = 0;
  let flipped = false;

  function render() {
    const data = demos[mode];
    const item = data.items[index];
    const head =
      `<div class="demo-head"><span>${data.title}</span><span>${index + 1} / ${data.items.length}</span></div>`;

    let body = "";
    if (mode === "quiz") {
      body = `
        <p class="demo-q">${item.q}</p>
        <label class="hint" for="demo-ans">Your answer</label>
        <input id="demo-ans" class="answer-box" type="text" placeholder="Type your answer…" autocomplete="off" />
        <div class="demo-nav" style="justify-content:flex-start;gap:8px;margin-top:12px">
          <button type="button" id="check-ans">Check answer</button>
          <button type="button" id="see-ans">See answer</button>
        </div>
        <p class="hint" id="demo-feedback" aria-live="polite"></p>`;
    } else if (mode === "practise") {
      body = `
        <p class="demo-q">${item.q}</p>
        <div class="demo-opts" role="list">
          ${item.options
            .map(
              (opt, i) =>
                `<button type="button" class="demo-opt" data-i="${i}"><strong>${String.fromCharCode(65 + i)}</strong> ${opt}</button>`
            )
            .join("")}
        </div>
        <p class="hint">Tap an option to check your answer</p>`;
    } else {
      body = `
        <p class="hint">Question</p>
        <button type="button" class="flip-card${flipped ? " back" : ""}" id="flip">
          ${flipped ? item.a : item.q}
        </button>
        <p class="hint">${flipped ? "Tap to show question" : "Tap the card to flip between question and answer"}</p>`;
    }

    const nav = `
      <div class="demo-nav">
        <button type="button" id="prev">← Prev</button>
        <button type="button" id="next">Next →</button>
      </div>`;

    stage.innerHTML = head + body + nav;
    wire();
  }

  function wire() {
    stage.querySelector("#prev")?.addEventListener("click", () => {
      index = (index - 1 + demos[mode].items.length) % demos[mode].items.length;
      flipped = false;
      render();
    });
    stage.querySelector("#next")?.addEventListener("click", () => {
      index = (index + 1) % demos[mode].items.length;
      flipped = false;
      render();
    });

    if (mode === "quiz") {
      const fb = stage.querySelector("#demo-feedback");
      stage.querySelector("#check-ans")?.addEventListener("click", () => {
        const val = (stage.querySelector("#demo-ans").value || "").trim().toLowerCase();
        const ok = val.includes(demos.quiz.items[index].answer);
        fb.textContent = ok ? "Correct." : "Not quite — try again or see the answer.";
        fb.style.color = ok ? "var(--ok)" : "var(--warn)";
      });
      stage.querySelector("#see-ans")?.addEventListener("click", () => {
        fb.textContent = "Answer: " + demos.quiz.items[index].answer;
        fb.style.color = "var(--muted)";
      });
    }

    if (mode === "practise") {
      stage.querySelectorAll(".demo-opt").forEach((btn) => {
        btn.addEventListener("click", () => {
          const i = Number(btn.dataset.i);
          const correct = demos.practise.items[index].answer;
          stage.querySelectorAll(".demo-opt").forEach((b) => {
            b.classList.remove("correct", "wrong");
            const bi = Number(b.dataset.i);
            if (bi === correct) b.classList.add("correct");
            else if (bi === i) b.classList.add("wrong");
          });
        });
      });
    }

    if (mode === "slideshow") {
      stage.querySelector("#flip")?.addEventListener("click", () => {
        flipped = !flipped;
        render();
      });
    }
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      mode = tab.dataset.demo;
      index = 0;
      flipped = false;
      tabs.forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
      render();
    });
  });

  const toggle = document.getElementById("nav-toggle");
  const mobile = document.getElementById("mobile-nav");
  toggle?.addEventListener("click", () => {
    const open = mobile.hasAttribute("hidden");
    if (open) mobile.removeAttribute("hidden");
    else mobile.setAttribute("hidden", "");
    toggle.setAttribute("aria-expanded", String(open));
  });
  mobile?.querySelectorAll("a").forEach((a) =>
    a.addEventListener("click", () => {
      mobile.setAttribute("hidden", "");
      toggle.setAttribute("aria-expanded", "false");
    })
  );

  document.querySelectorAll('a[href="#privacy"], a[href="#terms"]').forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const id = a.getAttribute("href").slice(1);
      document.getElementById(id)?.removeAttribute("hidden");
    });
  });
  document.querySelectorAll("[data-close-legal]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".legal").forEach((el) => el.setAttribute("hidden", ""));
    });
  });

  // Deep-link hash for /pricing and /contact routes
  const path = location.pathname.replace(/\/$/, "");
  if (path.endsWith("/pricing")) location.hash = "pricing";
  if (path.endsWith("/contact")) location.hash = "contact";

  render();
})();
