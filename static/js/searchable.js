function makeSearchable(selectOrId) {
    const select = typeof selectOrId === "string"
        ? document.getElementById(selectOrId)
        : selectOrId;

    if (!select || select.tagName !== "SELECT") return;
    if (select.multiple || select.dataset.noSearch === "1") return;
    if (select.dataset.searchReady === "1" || select.dataset.ready === "1") return;

    const realOptions = Array.from(select.options).filter((opt) => (opt.value || "").trim() !== "");
    const count = realOptions.length;
    const threshold = Number(select.dataset.searchThreshold || 4);

    // Unified UX rule:
    // <= threshold options: keep normal dropdown
    // > threshold options: searchable input (min 2 chars)
    if (count <= threshold) {
        select.dataset.searchReady = "1";
        select.dataset.ready = "1";
        select.dataset.searchMode = "dropdown";
        return;
    }

    select.dataset.searchReady = "1";
    select.dataset.ready = "1";
    select.dataset.searchMode = "search";
    select.style.display = "none";

    const wrapper = document.createElement("div");
    wrapper.className = "searchable-wrapper";

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = select.dataset.searchPlaceholder || (window.currentUiLang && window.currentUiLang() === 'ar' ? "اكتب أول حرفين للبحث..." : "Type 2 letters to search...");
    input.className = "searchable-input";
    input.autocomplete = "off";

    const dropdown = document.createElement("div");
    dropdown.className = "searchable-dropdown";

    function selectedText() {
        const opt = select.options[select.selectedIndex];
        if (!opt || !opt.value) return "";
        return opt.text || "";
    }

    function render(filter) {
        const q = (filter || "").toLowerCase().trim();
        dropdown.innerHTML = "";

        // Only suggest if at least 2 characters are typed
        if (q.length < 2) {
            dropdown.style.display = "none";
            return;
        }

        const options = Array.from(select.options).filter((opt) => {
            if (!opt.value) return false; // Skip empty/placeholder options
            const text = (opt.text || "").toLowerCase();
            return text.includes(q);
        });

        if (!options.length) {
            const empty = document.createElement("div");
            empty.className = "searchable-empty";
            empty.textContent = "No results";
            dropdown.appendChild(empty);
            dropdown.style.display = "block";
            return;
        }

        options.forEach((opt) => {
            const item = document.createElement("div");
            item.className = "searchable-item";
            item.textContent = opt.text;

            item.addEventListener("click", function () {
                select.value = opt.value;
                input.value = opt.text || "";
                dropdown.style.display = "none";
                select.dispatchEvent(new Event("change", { bubbles: true }));
            });

            dropdown.appendChild(item);
        });

        dropdown.style.display = "block";
    }

    input.value = selectedText();

    input.addEventListener("focus", function () {
        render(input.value);
    });

    input.addEventListener("input", function () {
        render(input.value);
    });

    input.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            dropdown.style.display = "none";
        }
    });

    select.addEventListener("change", function () {
        input.value = selectedText();
    });

    document.addEventListener("click", function (e) {
        if (!wrapper.contains(e.target)) {
            dropdown.style.display = "none";
        }
    });

    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(input);
    wrapper.appendChild(dropdown);
}

function enhanceAllSearchableSelects(root) {
    const scope = root || document;
    scope.querySelectorAll("select").forEach((select) => makeSearchable(select));
}

document.addEventListener("DOMContentLoaded", function () {
    enhanceAllSearchableSelects(document);
});

window.makeSearchable = makeSearchable;
window.setupSearchableSelect = makeSearchable;
window.enhanceAllSearchableSelects = enhanceAllSearchableSelects;
