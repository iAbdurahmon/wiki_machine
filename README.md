# wiki_machine

action api : for database, ask for article names and dates, database to be filled beforehand
rest api : for the HTML to be requested during use of the site


use action api to get the different versions of the articles, put them in a database before anyone uses the site, in the database, use mariadb system versioned tables to save the active time of articles. using the revid; request the HTML of the site or store the HTML (to be resovled) using rest.api

make a website frame for picking a specific date for the articles that we pick out
render the inside of the frame with the html given
