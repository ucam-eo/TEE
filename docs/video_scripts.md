# TEE Video Tutorial Scripts

Three screen-recording tutorials for TEE users. Record each as a Zoom
screen share with narration. Rehearse once, record in one take.

**Setup before recording:**
- Use michael (production) with a few pre-built viewports (Wicken Fen, Wytham Woods, etc.)
- Browser: Safari or Chrome, full screen, no bookmarks bar
- Have a ground-truth shapefile ready for Video 3
- Close all other tabs/notifications

---

## Video 1: Exploring Tessera Embeddings (5 min)

**Audience:** Casual users in demo mode (no account needed)
**Goal:** Show what TEE does and how to explore embeddings interactively

### Script

**[0:00] Title slide / intro**

> "Welcome to TEE — the Tessera Embeddings Explorer. TEE lets you
> interactively explore satellite embedding vectors produced by the
> Tessera foundation model. In this video, I'll show you how to browse
> and explore embeddings without needing an account."

**[0:20] Viewport selector page**
*Show the viewport selector with the map and viewport list*

> "This is the viewport selector. Each viewport is a geographic area
> where embeddings have been pre-computed. You can see the available
> viewports in the sidebar — each one covers roughly a 5 by 5 kilometre
> area."

*Click on a viewport (e.g., Wicken Fen) to highlight it on the map*

> "Let me select Wicken Fen, a nature reserve near Cambridge. You can
> see its location on the map."

**[0:45] Open the viewer**
*Click "Select" then the viewer button to open viewer.html*

> "Now let's open the viewer."

**[0:55] Six-panel overview**
*Pause to show all six panels*

> "The viewer has six panels. Top left is OpenStreetMap for geographic
> reference. Top middle shows satellite imagery — you can switch between
> ESRI and Google. Top right shows the Tessera embedding visualization —
> these colours represent the 128-dimensional embeddings projected to
> three colours using PCA."

*Point to bottom row*

> "Bottom left is a 3D PCA scatter plot — you can rotate it with right-
> drag and pan with left-drag. Bottom middle shows a change heatmap when
> comparing two years. And bottom right shows a second year for temporal
> comparison."

**[1:30] Navigate the map**
*Pan and zoom on any panel — show how all panels stay in sync*

> "All the geographic panels are synchronised. When I zoom in on the
> satellite view, all other panels zoom to the same location."

**[1:45] Year switching**
*Change the year dropdown on Panel 3*

> "I can switch between years using this dropdown. The embeddings update
> instantly because the data is pre-computed."

**[2:00] Click exploration**
*Single-click on a panel to place triangle markers*

> "A single click on any panel places a marker across all panels —
> useful for cross-referencing what a location looks like in satellite
> imagery versus the embedding space."

**[2:20] Similarity search**
*Double-click on the embedding panel*

> "Now the powerful part. If I double-click on a pixel, TEE runs a
> similarity search entirely in your browser. It finds all pixels in
> the viewport that have similar embeddings — shown in yellow on the
> satellite panel."

*Adjust the threshold slider*

> "This slider controls how similar pixels need to be. Moving it right
> includes more pixels; moving it left makes the search more selective.
> This all happens in real-time — no server calls needed."

**[2:55] PCA scatter plot interaction**
*Point to the highlighted points in Panel 4*

> "Notice the 3D scatter plot also highlights the matching points.
> This gives you a sense of where these similar pixels sit in the
> embedding space."

**[3:15] Change detection**
*Switch to change-detection mode using the mode selector*

> "Let me switch to change-detection mode. Now the bottom panels show
> a heatmap of how much each pixel changed between two years."

*Select different years in the two dropdowns*

> "I'll compare 2018 and 2024. Bright colours mean more change, dark
> means stable. The statistics panel shows the distribution of change."

**[3:50] Mode overview**
*Switch back to explore mode*

> "TEE has four modes: Explore for browsing, Change Detection for
> temporal analysis, Labelling for creating habitat labels, and
> Validation for evaluating classifiers. The labelling and validation
> modes require an account, which I'll cover in the next video."

**[4:10] Wrap-up**

> "That's TEE in explore mode. Everything you've seen runs in your
> browser with no account needed. The embeddings are downloaded once
> and cached locally, so similarity search is instant and private —
> your clicks never leave your browser."

> "In the next video, I'll show how to create your own viewports and
> build habitat labels."

**[4:30] End**

---

## Video 2: Creating Viewports and Labelling (8 min)

**Audience:** Users with an account
**Goal:** Create a viewport, label habitats, export labels

### Script

**[0:00] Intro**

> "In this video, I'll show you how to create your own viewport,
> label habitats using TEE's labelling tools, and export your labels.
> You'll need a TEE account for this — ask your administrator to
> create one."

**[0:15] Log in**
*Click Login, enter credentials*

> "I'll log in first. Once logged in, I can create and delete viewports."

**[0:25] Create a viewport**
*Click on the map to place a viewport box*

> "To create a viewport, I just click on the map. A preview box
> appears — this is the area that will be processed. I can also search
> for a place by name."

*Type a name in the search box (e.g., "Eddington") and select*

*Fill in the viewport name, select years (e.g., 2018 and 2024)*

> "I'll name it 'Eddington', select 2018 and 2024 for temporal
> comparison, and click Create."

**[0:55] Processing**
*Show the progress bar in the sidebar*

> "TEE now downloads embedding tiles from GeoTessera, creates map
> pyramids, and extracts vectors. This takes about 30 seconds to a
> minute depending on the viewport size. You can see the progress
> here — it shows what's happening at each stage."

*Wait for processing to complete (or cut/fast-forward)*

> "Once processing is done, TEE automatically opens the viewer."

**[1:30] Switch to labelling mode**
*Select "Labelling" from the mode dropdown*

> "I'll switch to labelling mode. The bottom-right panel now shows
> labelling controls."

**[1:45] Auto-labelling with K-means**
*In Panel 6, set k=5 and click Go*

> "The fastest way to start is auto-labelling. I'll set k to 5 and
> click Go. This runs K-means clustering on the embedding space
> entirely in your browser."

*Show the segmentation overlay appearing on Panel 5*

> "The clusters appear as coloured regions. Each cluster groups
> pixels with similar embeddings — which often correspond to similar
> land cover types."

**[2:20] Naming clusters**
*Type a name for a cluster in the input field*

> "I can name each cluster. Looking at the satellite imagery, this
> green cluster looks like cropland, so I'll type 'Cropland'."

**[2:40] Using a schema**
*Click the Schema dropdown, select UKHab or HOTW*

> "For standardised naming, I can load a schema. Let me select
> HOTW — Habitats of the World. Now I can browse the hierarchy
> and pick the right class."

*Browse the schema tree, select a label*

**[3:00] Promoting clusters to labels**
*Click the promote arrow on a cluster*

> "When I'm happy with a cluster's name, I click the arrow to
> promote it to a saved label. I can also promote all clusters
> at once."

**[3:20] Manual labelling**
*Switch to "Manual" in the label mode dropdown*

> "For more precise control, I'll switch to manual labelling mode.
> Panel 2 is now titled 'Create labelled points'."

**[3:35] Point labels**
*Set a label name and colour, then Ctrl+click on the map*

> "I'll set the active label to 'Woodland', pick a green colour,
> and Ctrl-click on a wooded area in the satellite panel. Each
> click places a point label."

**[3:55] Adjusting similarity threshold**
*Drag the class threshold slider*

> "Each label class has a similarity threshold. Moving this slider
> expands or contracts the area that matches — based on embedding
> similarity, not geographic distance."

**[4:15] Polygon labels**
*Ctrl+double-click to start polygon drawing, click vertices, double-click to finish*

> "For area labels, I Ctrl-double-click to start drawing a polygon.
> Click to add vertices, then double-click to close it. All pixels
> inside the polygon are labelled."

**[4:45] Classification**
*Click the Classify button*

> "With some labels defined, I can click Classify to see a full
> nearest-centroid classification across the viewport. Panel 5 now
> shows every pixel coloured by its nearest label class."

**[5:05] Panel 4 — label verification**
*Point to the PCA scatter plot, show coloured clusters*

> "The 3D scatter plot also colours by label class — giving you
> a quick visual check of whether your classes are separable in
> embedding space."

**[5:25] Timeline**
*Click the timeline icon on a label*

> "I can check how a label's coverage changes over time. Click
> the timeline button to see pixel counts across all available
> years. This is useful for monitoring land-use change."

**[5:45] Export**
*Click Export, show the dropdown, select ESRI Shapefile*

> "When I'm done, I can export my labels. The Export dropdown
> offers JSON for re-importing into TEE, GeoJSON for GIS tools,
> ESRI Shapefile for ArcGIS and QGIS, and a map image as JPG."

*Click ESRI Shapefile, show the downloaded ZIP*

**[6:05] Sharing labels**
*Click the Share button, show the dropdown*

> "I can also share my labels. In private mode, only the
> embeddings and label names are shared — no locations. This
> contributes to the Tessera global habitat directory. In public
> mode, geolocated labels are shared with other users on this
> server."

*Fill in name/email/org, click Submit*

**[6:30] Importing shared labels**
*Click Import, show "From shared labels" option*

> "Other users' public labels appear in the Import dropdown. I
> can import them with one click and they become regular labels
> I can edit and re-share."

**[6:50] Import from file**
*Click Import → From file, select the previously exported ZIP*

> "I can also import labels from a file — useful for loading
> labels exported from another viewport or shared by a colleague."

**[7:10] Wrap-up**

> "That covers viewport creation, auto and manual labelling,
> export, and sharing. In the next video, I'll show how to
> evaluate classifier performance using ground-truth shapefiles."

**[7:25] End**

---

## Video 3: Evaluating Classifiers (6 min)

**Audience:** Advanced users (researchers, data scientists)
**Goal:** Upload ground truth, run evaluation, interpret results

### Script

**[0:00] Intro**

> "In this video, I'll show how to evaluate machine learning
> classifiers on Tessera embeddings using TEE's validation mode.
> You'll need a ground-truth shapefile — a set of expert-labelled
> polygons covering part of your viewport."

**[0:15] Prepare ground truth**

> "I have a shapefile of habitat polygons for Wytham Woods. It
> was created in QGIS with a 'habitat' attribute column containing
> class names like 'Broadleaved woodland', 'Grassland', and so on.
> The file needs to be a ZIP containing the .shp, .dbf, .shx, and
> .prj files."

**[0:35] Switch to validation mode**
*Open viewer for Wytham Woods, select Validation mode*

> "I'll open the Wytham Woods viewport and switch to Validation mode.
> The layout changes — Panel 1 shows class statistics, Panel 4 will
> show learning curves, Panel 5 the confusion matrix, and Panel 6
> has the controls."

**[0:55] Upload shapefile**
*Drag and drop the ZIP onto the upload area in Panel 6*

> "I drag my shapefile ZIP into the upload area. TEE reads it,
> shows the available attribute fields, and overlays the polygons
> in red on the satellite panel."

**[1:15] Select the classification field**
*Select "habitat" from the field dropdown*

> "I select 'habitat' as the classification field. Panel 1 now
> shows the pixel count per class. Classes with very few pixels
> will be excluded from evaluation — you need at least 50 pixels
> per class."

**[1:35] Configure classifiers**
*Check/uncheck classifier checkboxes*

> "I can choose which classifiers to evaluate. Let me select k-NN,
> Random Forest, and MLP. Each one can be configured — click the
> expand button to see hyperparameters."

*Expand RF settings, show n_estimators and max_depth*

> "For Random Forest, I can set the number of trees and maximum
> depth. The defaults work well for most cases."

**[2:00] Set max training pixels**
*Adjust the max_train slider or input*

> "The max training pixels controls the largest training set size.
> More pixels give better accuracy estimates but take longer. The
> default of 10,000 is a good balance."

**[2:15] Run evaluation**
*Click Run Evaluation*

> "I click Run. TEE sends the data to the server, which trains
> each classifier at increasing sample sizes with 5-fold cross-
> validation. Results stream back in real-time."

**[2:35] Interpret learning curves**
*Point to the Chart.js chart as lines appear*

> "The chart shows F1 score versus training set size on a log scale.
> Each classifier gets a coloured line with a shaded confidence band.
> You can see how accuracy improves with more training data."

*Wait for a few data points to appear*

> "Random Forest typically does well with few samples. MLP may catch
> up with more data. K-NN gives a useful baseline."

**[3:10] Confusion matrix**
*Point to Panel 5 showing the confusion matrix*

> "Panel 5 shows the confusion matrix for the largest training size.
> The diagonal shows correct predictions. Off-diagonal cells show
> which classes get confused with each other."

*Hover over a cell*

> "I can hover over cells to see exact counts. This helps identify
> which habitat classes are hard to distinguish — often because
> their embeddings overlap."

**[3:40] Finish a classifier early**
*Click the "Finish" button next to a classifier*

> "If I'm satisfied with a classifier's performance, I can click
> Finish to train it on all available data. This creates a
> downloadable model."

**[4:00] Download the model**
*Click the download button*

> "Once a classifier is finished, I can download the trained model
> as a joblib file. This can be used in Python with scikit-learn
> to classify embeddings anywhere — not just in TEE."

**[4:20] Try different metrics**
*Switch between Macro F1 and Weighted F1*

> "The metric toggle lets me switch between macro-averaged F1 —
> which treats all classes equally — and weighted F1, which
> accounts for class imbalance."

**[4:35] Re-run with different settings**
*Change a hyperparameter and click Run again*

> "I can adjust hyperparameters and re-run. Each run overwrites
> the previous results, so experiment freely."

**[4:50] Using exported labels as ground truth**

> "A useful workflow is to create labels in labelling mode, export
> them as an ESRI Shapefile, then upload that same file in
> validation mode. This lets you evaluate how well your manual
> labels generalise across the viewport."

**[5:10] Wrap-up**

> "That's TEE's validation pipeline. It gives you a quick,
> interactive way to benchmark how well Tessera embeddings
> separate your habitat classes — and download trained models
> for use in production pipelines."

> "For more details, check the documentation linked in the
> README on GitHub. Thanks for watching."

**[5:30] End**

---

## Recording Tips

1. **Resolution:** Record at 1920x1080. Zoom's screen share works well.
2. **Mouse:** Move slowly and deliberately. Pause before and after clicks.
3. **Narration:** Read the script naturally — don't rush. Silence is fine
   during loading/processing (just note what's happening).
4. **Mistakes:** If you make a small mistake, just continue. For big
   mistakes, pause, say "let me try that again", and redo the section —
   minor edits are easy if recording in Zoom.
5. **Processing waits:** For the 30s viewport processing, either:
   - Use a pre-built viewport and skip the wait
   - Fast-forward in editing (add a "processing..." caption)
   - Use the time to explain what's happening
6. **Post-recording:** Share the Zoom cloud recording link with your
   admin assistant for uploading to the channel.
