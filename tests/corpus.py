"""
Corpus de validation — intitulés RÉELS extraits de l'API TUNEPS (avis 2026).

Étiquettes :
  "yes"    -> vraie opportunité pour un fabricant de drapeaux / banderoles /
              guirlandes / impression textile. Doit sortir en « forte ».
  "maybe"  -> lié au métier mais incertain (enseignes, signalétique, lumineux).
              Acceptable en « à vérifier » ; ne doit pas être en « forte ».
  "no"     -> hors sujet. Ne doit pas être signalé du tout.
"""

CORPUS: list[tuple[str, str]] = [
    # ---------------------------------------------------------------- yes
    ("Acquisition du drapeau national de la République tunisienne ~ اقتناء العلم الوطني للجمهورية التونسية", "yes"),
    ("Acquisition des drapeaux nationaux ~ إقتناء رايات وطنية", "yes"),
    ("acquisition drapeaux national ~ اقتناء اعلام الراية الوطنية", "yes"),
    ("Achat drapeaux administratifs et des guirlands ~ اقتناء اعلام وأشرطة زينة", "yes"),
    ("acquisition drapeaux et de guirlande de décoration ~ إقتناء أعلام و شرائط زينة", "yes"),
    ("Acquisition de drapeaux décoratifs et de banderoles ~ اقتناء أعلام زينة ولافتات", "yes"),
    ("Acquisition de banderoles et drapeaux au profit de la commune de Sidi Bannour ~ إقتناء لافتات و أعلام زينة", "yes"),
    ("Acquisition de mâts de drapeau ~ إقتناء أعمدة أعلام", "yes"),
    ("Consultation d’acquisition des poteaux porte-drapeaux", "yes"),
    ("Acquisition des drapeaux et oriflammes", "yes"),
    ("Acquisition drapeau e oriflamme ~ اقتناء اعلام و زينة للمناسبات", "yes"),
    ("Acquisition des produit de pavoisement pour l'annee 2026 ~ اقتناء اعلام زينة لسنة 2026", "yes"),
    ("Achat banderoles a impression numériques en tissu de haute qualité ~ اقتناء لافتات طباعة رقمية من القماش الرفيع", "yes"),
    ("Confection de tampon et impression sur tissu des banderoles", "yes"),
    ("Acquisition de banderoles en tissu pour les occasions nationales et religieuses et des drapeaux administratifs", "yes"),
    ("Consultation de Fourniture et Impression des bâches avec Anneaux ~ استشارة لإقتناء لافتات مع الطباعة عليها من القماش", "yes"),
    ("calligraphie des panneaux en tissu et galvanisé 2026 ~ تخطيط لافتات قماش و حديدية لسنة 2026", "yes"),
    ("اقتناء لافتات وشرائط أعلام وطيلة سنة 2026", "yes"),
    ("إقتناء أعلام و أشرطة زينة", "yes"),
    ("اقتناء أعلام وطنية وإعداد اللافتات المتعلقة بمختلف الحفلات العمومية لسنة 2026", "yes"),
    ("إقتناء لافتات و أشرطة و أعلام زينة لفائدة بلدية سيدي عامر مسجد عيسى", "yes"),
    ("شراء علامات زينة شوارع و اعداد لافتات بمناسبة الحفلات العمومية", "yes"),
    ("اقتناء مواد الزينة ولافتات لمختلف المناسبات الوطنية و الاعياد الدينية والتظاهرات بالمنطقة البلدية", "yes"),
    ("استشارة عدد 09/2026 على الخط وخارج الخط لخياطة واقتناء اعلام ومواد زينة", "yes"),
    ("Acquisition de bannières et de rubans décoratifs 2026 ~ اقتناء واللافتات و شرائط الزينة لفائدة بلدية فرنانة", "yes"),
    ("اقتناء لافتات اشهارية feather flag", "yes"),
    ("''Roll up'' اقتناء لافتات ومعلقات و", "yes"),
    ("كتابة لافتات إشهارية لفائدة بلدية بني مطير لسنة 2026", "yes"),
    ("اقتناء و تركيب سارية حاملة للراية الوطنية", "yes"),
    ("Fourniture et installation d’une hampe porte-drapeau extérieure", "yes"),
    # fautes de frappe réelles
    ("Avquisition drapeaux ~ اقتناء أعلام", "yes"),
    ("acquisition des drapeux", "yes"),
    ("aquisition des drapeaux ~ اقتناء الراية الوطنية", "yes"),
    ("achat des drappeaux au profit de l administration de la justice", "yes"),
    ("Drapeaux Tunisiee ~ أعلام", "yes"),
    ("CONSULTATION POUR L'ACQUISITION DES DRAPEAUX ADMINISTRATIFS DES DECORATION ET DES BANDROLLES SELON TEXTE", "yes"),
    ("acquistion de drapeaux ~ إقتناء أعلام زينة", "yes"),

    # -------------------------------------------------------------- maybe
    # signalétique / enseignes : parfois du tissu, souvent du métal
    ("Impression Banderoles, panneaux de signalisation et cachets ~ طباعة اللافتات واللوحات التوجيهية والطوابع الإدارية", "maybe"),
    ("Acquisition des timbres en caoutchouc et des banderoles", "maybe"),
    ("conception ,realisation et impression de banderoles et affiches pour colloques ~ لافتتات ومعلقات بمناسبة ملتقى علمي", "maybe"),
    ("اقتناء طوابع إدارية و لافتات إعلانية بعنوان 2026", "maybe"),
    ("صناعة لافتات من معدن", "maybe"),
    ("إقتناء لافتات توجيهية", "maybe"),
    ("Acquisition Des Panneaux De Signalisation Routière ~ اقتناء لافتات توجيهية مرورية", "maybe"),
    ("ENSEIGNE LUMINEUSE ~ اقتناء لافتات مضيئة", "maybe"),
    ("أشغال تركيز الزينة الضوئية لسنة 2026 ببلديّة زغوان", "maybe"),
    ("Aqcuisition Décoration Lumineuse Ville Antique 2026 ~ استشارة اقتناء مواد الزينة الضوئية للمدينة العتيقة", "maybe"),
    ("Lot de piéces pour guirlandes Pont Morris", "maybe"),
    ("إقتناء لافتات تشوير الطرقات", "maybe"),

    # ----------------------------------------------------------------- no
    # plantes ornementales — piège n°1 (« نباتات زينة »)
    ("استشارة اقتناء نباتات زينة", "no"),
    ("إستشارة إقتناء نباتات الزينة لبلدية المعمورة لسنة 2026", "no"),
    ("اقتناء نباتات و أشجار زينة خاصة بالمناطق الخضراء", "no"),
    ("Acquisition des plantes d'ornement ~ إقتناء نباتات زينة", "no"),
    ("ACHAT DES PLANTES ARTIFICIELLES ~ إقتناء نباتات زينة", "no"),
    ("Acquisition des Bacs à fleurs ~ اقتناء أحواض نباتات الزينة", "no"),
    ("taille des arbres d'hornements ~ زبر اشجار الزينة", "no"),
    ("Acquisition d'engrais de semences de gazon et de plantes ornementales ~ اقتناء أسمدة و مبيدات و نباتات زينة", "no"),
    ("Taille des arbres et des palmiers dans la ville de Mahdia ~ تقليم أشجار الزينة و النخيل بمدينة المهدية", "no"),
    ("اقتناء عشب طبيعي و اجشار زينة", "no"),
    ("galet de décoration ~ حجارة زينة", "no"),
    # trésorerie / coffre — « خزينة » contient « زينة »
    ("أشغال تهيئة خزينة الأسلحة بمقر إقليم الأمن الوطني بنابل", "no"),
    ("مشروع تهيئة مقر الإدارة الفرعية للخزينة والتقاعد ببن عروس.", "no"),
    ("مراقبة فنية لمشروع احداث خزينة اسلحة بمدرسة الشرطة سيدي سعد", "no"),
    # informatique — « إعلامية » contient « أعلام »
    ("Acquisition matériels informatique ~ اقتناء معدات اعلامية", "no"),
    ("consommable Informatique ~ اقتناء لوازم أعلامية", "no"),
    ("matériel informatique ~ شراء معدات أعلامية", "no"),
    ("أقتناء تجهيزات أعلامية لفائدة ولاية تطاوين و المعتمديات التابعة لها", "no"),
    ("Travaux de récablage de reseau informatique ~ توسيع شبكة الأعلامية بالمستشفى المحلي", "no"),
    ("اقتناء المواد الأعلامية والتصويرية والطباعة سنة 2026", "no"),
    # divers hors sujet
    ("Travaux d’aménagement des carrefours dans la Commune de Djerba Houmt Souk", "no"),
    ("Acquisition de mobiliers de bureau pour l’année 2017", "no"),
    ("TRAVAUX D’ECLAIRAGE PUBLIC AWLED ALI COMMUNE ENNOUR KASSERINE", "no"),
    ("Acquisition de pièces de rechange pour véhicule léger Audi A4 B9 1.4 TFSI", "no"),
    ("curage de réseaux d’eaux pluvial", "no"),
    ("achats produit alimentaire ~ شراء مواد غذائية", "no"),
    ("Acquisition de matériel médical", "no"),
    ("Machine a laver automatique 17 kg", "no"),
    ("réhabilitation et entretien de l école primaire bazina joumine /BIZERTE", "no"),
    ("Ouverture, curage et nettoyage du cours d’eau d’Oued El Hamraya", "no"),
    ("اقتناء عدد 400 جراية صحية لفائدة إقليم الحرس الوطني بتطاوين", "no"),
    ("شراء كتب مدرسية", "no"),
    ("photocopieur grand tirage", "no"),
    ("« ACHAT DES PC PORTABLES »", "no"),
    ("Acquisition Article quincaillerie pour la direction port de Rades", "no"),
    ("main d'oeuvre ~ يد عاملة", "no"),
]
