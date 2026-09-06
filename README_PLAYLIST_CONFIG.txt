Heatwarped Music Patcher - playlist_config.json

playlist_mode:
  stock  = keep the original 4 Heatwarped playlists unchanged
  full   = put every available music track in every playlist
  custom = use the IDs listed in playlist_config.json

Track IDs:
  01-08 = stock Heatwarped tracks
  09    = WARPED (protected, cannot be put in a playlist)
  10+   = custom tracks in the final patcher order

Custom mode example:
  "MainMenu": [10]
  "Racing": [1, 2, 3, 4, 5, 6, 7, 8, 11, 12]

This lets a song play only in the menu, for example, by putting its ID in MainMenu and omitting it from Racing/Drift/FreeRoam.
The normal config.json no longer contains playlist_mode.

------------------------------------------------------------------------------------------------------------------------------

You can download the NFS:U2 OST and just drop the songs as is in the tracks folder:
https://archive.org/details/need-for-speed-underground-2-original-soundtrack

WARNING :
02. Capone - I Need Speed
17. The Bronx - Notice of Eviction
ARE MISSING IN THE INTERNET ARCHIVE'S LINK. Just DL them appart and name them like I did.

I Need Speed       : https://youtu.be/g7WNlNwmOlM
Notice of Eviction : https://youtu.be/7FiA-n-Yyts

Youtube DL site    : https://notube.lol



The playlist_config.json is already configured to create the playlists like in NFS:U2 using this indexes:

01 - Snoop Dogg and The Doors - Riders on the Storm
02 - Capone - I Need Speed
03 - Chingy - I Do
04 - Sly Boogy - That'z My Name
05 - Xzibit - LAX
06 - Terror Squad - Lean Back
07 - Fluke - Switch/Twitch
08 - Christopher Lawrence - Rush Hour
09 - Felix Da Housecat - Rocket Ride
10 - Sin - Hard EBM
11 - FREELAND - Mind Killer
12 - Paul Van Dyk - Nothing But You
13 - Sonic Animation - E-Ville
14 - Killing Joke - The Death & Resurrection Show
15 - Rise Against - Give It All
16 - Killradio - Scavenger
17 - The Bronx - Notice of Eviction
18 - Ministry - No W
19 - Queens of the Stone Age - In My Head
20 - Mudvayne - Determined
21 - Septembre - I Am Weightless
22 - Helmet - Crashing Foreign Cars
23 - Cirrus - Back on a Mission
24 - Spiderbait - Black Betty
25 - Skindred - Nobody
26 - Snapcase - Skeptic
27 - Unwritten Law - The Celebration Song