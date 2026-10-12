// The GitHub part of the "Create a project" sheet (newproject.js): how work lands, and where a repository
// clones. GET /api/github[?folder=] → {signed_in, login, why, repos, remote}: the person's repositories as
// completion for "From GitHub" when gh is signed in, and whether the folder has a GitHub remote. Pull requests
// ("eki opens pull requests on GitHub") can be picked from GitHub, for a folder with a GitHub remote, or when
// "Also create a GitHub repository" is checked (Start new, its folder proposed, gh signed in). GET
// /api/github/clone-to?repo= says "Clones to ~/Projects/<name>", "Uses your clone at …" or the error as you type.
// newProjectGitHub({q, S, target, esc, trim, live}) is made per sheet; G = {gh, remote, made} is its state.
(function () {
  const tilde = (f) => String(f || "").replace(/^.*(\/Projects\/[^/]+)$/, "~$1");
  const SIGN_IN = "Sign in with `gh auth login` to see your repositories";

  window.newProjectGitHub = function ({ q, S, target, esc, trim, live }) {
    let ghSeq = 0, destSeq = 0, ghTimer = null;
    // gh: GET /api/github's answer; remote: the folder's GitHub remote; made: a project made with a warning
    const G = { gh: {}, remote: null, made: null };

    // whether pull requests can be picked now, and why not
    function prAllowed() {
      if (S.mode === "github" || G.remote) return [true, ""];
      if (!q("#np-repo-box").hidden && q("#np-create-repo").checked) return [true, ""];
      if (!G.gh.signed_in) return [false, "Pull requests need gh: sign in with `gh auth login`"];
      return [false, S.mode === "new" && !target() ? "Pull requests need a GitHub repository: create one below"
                                                   : "Pull requests need a folder with a GitHub remote"];
    }

    // what the GitHub choices show, from the mode, gh's status, the folder's remote and the checkboxes
    function refresh() {
      const gitHub = S.mode === "github";
      q("#np-github-box").hidden = !gitHub;
      q("#np-name").placeholder = gitHub ? "Optional — the repository's name" : "A name";
      q("#np-repos").innerHTML = (G.gh.repos || []).map((r) => `<option value="${esc(r)}"></option>`).join("");
      q("#np-gh-note").textContent = G.gh.signed_in ? "" : SIGN_IN;
      q("#np-repo-box").hidden = S.mode !== "new" || !G.gh.signed_in || !!target();
      q("#np-private-box").hidden = q("#np-repo-box").hidden || !q("#np-create-repo").checked;
      const [ok, why] = prAllowed();
      const pr = q("#np-lands-github");
      pr.disabled = !ok;
      if (!ok && pr.checked) { pr.checked = false; q("#np-lands-here").checked = true; }
      q("#np-pr-box").className = ok ? "np-pick" : "np-pick off";
      q("#np-lands-why").textContent = ok ? "" : why;
    }

    // gh's status, and the folder's GitHub remote; a later call wins
    async function lookGh(folder) {
      const mine = ++ghSeq;
      try {
        const r = await fetch("/api/github" + (folder ? "?folder=" + encodeURIComponent(folder) : ""));
        const got = await r.json();
        if (mine !== ghSeq || !live()) return;
        G.gh = got || {};
        G.remote = G.gh.remote || null;
        refresh();
      } catch (e) { /* no status this time: pull requests stay off for a folder */ }
    }

    function ghSoon() {
      G.remote = null;
      refresh();
      clearTimeout(ghTimer);
      const f = target();
      ghTimer = setTimeout(() => lookGh(f), 300);
    }

    async function lookDest() {
      const repo = trim(q("#np-github").value);
      const mine = ++destSeq;
      const line = q("#np-dest");
      if (!repo) { line.textContent = ""; line.className = "np-note"; return; }
      try {
        const r = await fetch("/api/github/clone-to?repo=" + encodeURIComponent(repo));
        const got = await r.json();
        if (mine !== destSeq) return;
        line.className = got.error ? "np-note bad" : "np-note";
        line.textContent = got.error ? got.error
          : got.reuse ? "Uses your clone at " + tilde(got.folder) : "Clones to " + tilde(got.folder);
      } catch (e) { /* said again on the next keystroke */ }
    }

    return { G, refresh, lookGh, ghSoon, lookDest, prAllowed };
  };
})();
