<!DOCTYPE html>
<html lang="fr">
	<head>
		<meta charset="utf-8">
		<meta name="viewport" content="width=device-width, initial-scale=1">
		<title>Bootstrap demo</title>
		<link rel="stylesheet" href="/assets/css/style.css">
		<link rel="preconnect" href="https://fonts.googleapis.com">
		<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
		<link href="https://fonts.googleapis.com/css2?family=Poppins:ital,wght@0,100;0,200;0,300;0,400;0,500;0,600;0,700;0,800;0,900;1,100;1,200;1,300;1,400;1,500;1,600;1,700;1,800;1,900&family=Roboto:ital,wght@0,100..900;1,100..900&display=swap" rel="stylesheet">

	</head>
	<body>
  		<nav class="navbar bg-body-tertiary">
			<div class="logo">
				<img src="/assets/img/logo.webp">
			</div>
			<div class="name">
				Ekoraid
			</div>
		</nav>

		<section>
			<h1>Devine le lieu:</h1>
			<form action="index.php" method="POST">
				<input type="text" name="lieu" placeholder="Ex: Doubs, Bas-Rhin ...">
			</form>


<?php
$lieu = "ardeche";

if( isset($_POST["lieu"]) && !empty($_POST["lieu"]) ) {
	$guess = $_POST["lieu"];
	$guess = strtolower($guess);
	$guess = str_replace("è", "e", $guess);
	$guess = str_replace("é", "e", $guess);

	if($lieu === "ardeche") {
		echo "<p>Félicitation !!!</p>";
		echo '<iframe src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d2818.59921883855!2d4.392654075216349!3d45.053354260758695!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x47f582c0cead31b1%3A0xa2b406d6fd56610f!2sCamping%20du%20Lac%20de%20Devesset!5e0!3m2!1sfr!2sfr!4v1765024671043!5m2!1sfr!2sfr" width="600" height="450" style="border:0;" allowfullscreen="" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>';
	}
}

?>


		</section>
	</body>
</html>