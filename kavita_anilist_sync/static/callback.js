(async () => {
  const message = document.getElementById("message");
  const token = new URLSearchParams(location.hash.slice(1)).get("access_token");
  history.replaceState(null, "", location.pathname);
  let session = null;
  try { session = JSON.parse(localStorage.getItem("kavita-user"))?.token; } catch {}
  if (!token || !session) {
    message.textContent = token ? "Log in to Kavita first, then connect AniList again." : "AniList did not return a token.";
    return;
  }
  const response = await fetch("api/me", {
    method: "PUT",
    headers: {"Content-Type": "application/json", "X-Requested-With": "kas", Authorization: `Bearer ${session}`},
    body: JSON.stringify({anilist_token: token}),
  });
  if (!response.ok) { message.textContent = "Could not save the connection. Try again from Kavita."; return; }
  location.replace(new URL("..", location.href).pathname);
})();
