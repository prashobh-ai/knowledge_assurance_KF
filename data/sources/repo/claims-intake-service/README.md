# Claims intake service

This service receives roadside claim intake forms from the mobile app and creates the claim record. It implements story RC-231.

The service checks that location, vehicle details and two photos are present, then returns a claim reference number to the app.

Run the unit tests before every merge. The main branch is protected and needs one review.
