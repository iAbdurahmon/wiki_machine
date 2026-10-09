# wiki_machine

action api : for database, ask for article names and dates, database to be filled beforehand
rest api : for the HTML to be requested during use of the site


use action api to get the different versions of the articles, put them in a database before anyone uses the site, in the database, use mariadb system versioned tables to save the active time of articles. using the revid; request the HTML of the site or store the HTML (to be resovled) using rest.api

make a website frame for picking a specific date for the articles that we pick out
render the inside of the frame with the html given (somewhat done!)



put at the top of each HTML:
    <meta name="viewport" content="width=device-width, initial-scale=1">
    
    <link rel="stylesheet" href="https://en.wikipedia.org/w/load.php?lang=en&modules=skins.minerva.styles%7Csite.styles&only=styles&skin=minerva">

    <style>
    html, body { margin: 0; padding: 8px; }
    </style>
   
    <base href="https://en.wikipedia.org/wiki/">

    <link rel="stylesheet" href="https://en.wikipedia.org/w/load.php?lang=en&modules=site.styles&only=styles&skin=minerva">
    
