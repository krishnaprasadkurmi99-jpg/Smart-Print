import pymupdf 

def analyze_pdf(file_path):
    print(f"Scanning document: {file_path}...\n")
    
    try:
        doc = pymupdf.open(file_path)
        total_pages = len(doc)
        color_pages = 0
        bw_pages = 0

        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            
            # Render a low-resolution image of the page to scan pixels quickly
            pix = page.get_pixmap(matrix=pymupdf.Matrix(0.2, 0.2)) 
            
            # Get a dictionary of all unique colors used on this page
            # Returns data like {(R, G, B): pixel_count}
            page_colors = pix.color_count(colors=True)
            
            is_bw = True
            
            # Check every color found on the page
            for color_tuple in page_colors.keys():
                # color_tuple[:3] gives us just the (R, G, B) values, ignoring Alpha/Transparency
                r, g, b = color_tuple[:3]
                
                # In true grayscale, R, G, and B are exactly equal (e.g., White is 255, 255, 255).
                # We allow a small threshold of 10 to account for compression artifacts.
                if max(r, g, b) - min(r, g, b) > 10:
                    is_bw = False
                    break # We found a real color! Stop checking this page.

            if is_bw:
                bw_pages += 1
                print(f"Page {page_num + 1} is: Black & White")
            else:
                color_pages += 1
                print(f"Page {page_num + 1} is: Color")

        # Pricing
        price_per_bw = 2.00
        price_per_color = 10.00
        total_price = (bw_pages * price_per_bw) + (color_pages * price_per_color)

        print("\n" + "="*30)
        print("📊 PRINTING SUMMARY")
        print("="*30)
        print(f"Total Pages: {total_pages}")
        print(f"B&W Pages:   {bw_pages} (Cost: ₹{bw_pages * price_per_bw})")
        print(f"Color Pages: {color_pages} (Cost: ₹{color_pages * price_per_color})")
        print(f"Total Amount Due: ₹{total_price}")
        print("="*30)

    except Exception as e:
        print(f"Error reading PDF: {e}")

analyze_pdf("test.pdf")