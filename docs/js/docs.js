// d2ha — Documentation pages: menu on phones, "On this page", copy buttons.
(function () {
  document.documentElement.classList.add('js');

  document.addEventListener('DOMContentLoaded', function () {
    var year = document.getElementById('year');
    if (year) year.textContent = new Date().getFullYear();

    // Menu: a drawer on phones (without JavaScript the link jumps to the list).
    var toggle = document.querySelector('.menu-toggle');
    var sidebar = document.querySelector('.docs-sidebar');
    function setOpen(open) {
      document.body.classList.toggle('nav-open', open);
      if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    if (toggle && sidebar) {
      toggle.addEventListener('click', function (event) {
        event.preventDefault();
        setOpen(!document.body.classList.contains('nav-open'));
      });
      document.addEventListener('click', function (event) {
        if (document.body.classList.contains('nav-open') &&
            !sidebar.contains(event.target) && !toggle.contains(event.target)) {
          setOpen(false);
        }
      });
      document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') setOpen(false);
      });
    }

    // On this page: built from the h2 headings, the current one highlighted.
    var toc = document.querySelector('.docs-toc');
    var headings = Array.prototype.slice.call(document.querySelectorAll('.docs-content h2[id]'));
    if (toc && headings.length > 1) {
      var title = document.createElement('div');
      title.className = 'toc-title';
      title.textContent = 'On this page';
      var list = document.createElement('ul');
      var links = headings.map(function (heading) {
        var item = document.createElement('li');
        var link = document.createElement('a');
        link.href = '#' + heading.id;
        link.textContent = heading.textContent;
        item.appendChild(link);
        list.appendChild(item);
        return link;
      });
      toc.appendChild(title);
      toc.appendChild(list);

      var current = -1;
      function highlight() {
        var index = 0;
        headings.forEach(function (heading, i) {
          if (heading.getBoundingClientRect().top < 120) index = i;
        });
        if (index !== current) {
          if (current >= 0) links[current].classList.remove('active');
          links[index].classList.add('active');
          current = index;
        }
      }
      window.addEventListener('scroll', highlight, { passive: true });
      highlight();
    }

    // Copy buttons on code blocks.
    if (navigator.clipboard) {
      document.querySelectorAll('.docs-content pre').forEach(function (pre) {
        var wrap = document.createElement('div');
        wrap.className = 'code-wrap';
        pre.parentNode.insertBefore(wrap, pre);
        wrap.appendChild(pre);
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'copy';
        button.textContent = 'Copy';
        button.addEventListener('click', function () {
          navigator.clipboard.writeText(pre.innerText.replace(/\n$/, '')).then(function () {
            button.textContent = 'Copied';
            setTimeout(function () { button.textContent = 'Copy'; }, 1500);
          });
        });
        wrap.appendChild(button);
      });
    }
  });
})();
