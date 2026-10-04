# wiki_machine

action api : for database, ask for article names and dates, database to be filled beforehand
rest api : for the HTML to be requested during use of the site

curl -s -A "WikiMachine/0.1 (student project; your@email)" \
  "https://en.wikipedia.org/w/api.php?action=query&prop=revisions&titles=Albert_Einstein&rvprop=ids|timestamp|user|comment&rvlimit=5&format=json&formatversion=2"
